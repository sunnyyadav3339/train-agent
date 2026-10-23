"""A tiny in-process stand-in for the Telegram Bot API, for offline handoff tests."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeTelegram:
    def __init__(self, chat_id: int = 42):
        self.chat_id = chat_id
        self.sent: list[tuple[str, dict]] = []   # (method, params) the agent sent
        self.queue: list[dict] = []              # updates to hand out on getUpdates
        self.next_update = 1
        self.next_message = 100
        self.on_send = None                      # callback(method, params, message_id) to script replies
        self.lock = threading.Lock()
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                method = self.path.rsplit("/", 1)[-1]
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                ctype = self.headers.get("Content-Type", "")
                params = json.loads(body or b"{}") if "json" in ctype else {"multipart": True, "bytes": len(body),
                                                                           "has_markup": b"reply_markup" in body}
                result = fake.handle(method, params)
                out = json.dumps({"ok": True, "result": result}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def push_text(self, text: str, chat_id: int | None = None) -> None:
        with self.lock:
            self.queue.append({"update_id": self.next_update,
                               "message": {"message_id": 1, "chat": {"id": chat_id or self.chat_id}, "text": text}})
            self.next_update += 1

    def push_button(self, data: str, message_id: int, chat_id: int | None = None) -> None:
        with self.lock:
            self.queue.append({"update_id": self.next_update, "callback_query": {
                "id": f"cb{self.next_update}", "data": data,
                "message": {"message_id": message_id, "chat": {"id": chat_id or self.chat_id}}}})
            self.next_update += 1

    def handle(self, method: str, params: dict):
        if method == "getUpdates":
            offset = params.get("offset", 0)
            with self.lock:
                self.queue = [u for u in self.queue if u["update_id"] >= offset]
                return list(self.queue)
        if method in ("sendMessage", "sendPhoto", "sendDocument"):
            with self.lock:
                mid = self.next_message
                self.next_message += 1
                self.sent.append((method, params))
            if self.on_send:
                self.on_send(method, params, mid)
            return {"message_id": mid}
        self.sent.append((method, params))
        return True

    def close(self) -> None:
        self.server.shutdown()
