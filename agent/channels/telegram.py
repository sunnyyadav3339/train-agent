"""Telegram Bot API client using only the standard library.

The agent's own channel to a human: it sends questions with a screenshot (and buttons for
approvals), waits for the reply, and sends run updates. Only messages from the configured
chat are ever read, so nobody else can answer for you.

    python -m agent.channels.telegram setup   # after messaging your bot once: prints your chat id
    python -m agent.channels.telegram test    # sends a test question and waits for your answer
"""

from __future__ import annotations

import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

API = "https://api.telegram.org"


class TelegramError(RuntimeError):
    pass


class Telegram:
    def __init__(self, token: str, chat_id: str | int | None = None, api: str = API):
        if not token:
            raise TelegramError("TELEGRAM_BOT_TOKEN is empty")
        self.base = f"{api}/bot{token}"
        self.chat_id = str(chat_id) if chat_id not in (None, "") else None
        self.offset: int | None = None

    # --- transport ----------------------------------------------------------
    def _call(self, method: str, params: dict | None = None, files: dict | None = None, timeout: float = 40):
        url = f"{self.base}/{method}"
        if files:
            body, ctype = _multipart(params or {}, files)
            req = urllib.request.Request(url, data=body, headers={"Content-Type": ctype})
        else:
            data = json.dumps(params or {}).encode()
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            raise TelegramError(f"{method} failed: HTTP {exc.code} {detail[:300]}") from exc
        except urllib.error.URLError as exc:
            raise TelegramError(f"{method} failed: {exc.reason}") from exc
        if not payload.get("ok"):
            raise TelegramError(f"{method} failed: {payload.get('description')}")
        return payload["result"]

    # --- sending -------------------------------------------------------------
    def send_text(self, text: str, buttons: list[list[tuple[str, str]]] | None = None) -> dict:
        params = {"chat_id": self.chat_id, "text": text[:4096]}
        if buttons:
            params["reply_markup"] = _keyboard(buttons)
        return self._call("sendMessage", params)

    def send_photo(self, png: bytes, caption: str = "", buttons: list[list[tuple[str, str]]] | None = None) -> dict:
        params = {"chat_id": self.chat_id, "caption": caption[:1024]}
        if buttons:
            params["reply_markup"] = json.dumps(_keyboard(buttons))
        return self._call("sendPhoto", params, files={"photo": ("screen.png", png, "image/png")}, timeout=60)

    def send_document(self, path: str, caption: str = "") -> dict:
        with open(path, "rb") as f:
            data = f.read()
        name = os.path.basename(path)
        return self._call("sendDocument", {"chat_id": self.chat_id, "caption": caption[:1024]},
                          files={"document": (name, data, "application/octet-stream")}, timeout=120)

    # --- receiving -----------------------------------------------------------
    def updates(self, timeout: int = 25) -> list[dict]:
        params = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
        if self.offset is not None:
            params["offset"] = self.offset
        result = self._call("getUpdates", params, timeout=timeout + 15)
        if result:
            self.offset = result[-1]["update_id"] + 1
        return result

    def drain(self) -> None:
        """Skip anything sent before now, so old messages are never taken as answers."""
        while self.updates(timeout=0):
            pass

    def _mine(self, update: dict) -> bool:
        chat = (update.get("message") or (update.get("callback_query") or {}).get("message") or {}).get("chat", {})
        return str(chat.get("id")) == self.chat_id

    def wait_reply(self, timeout_s: float, after_message_id: int | None = None) -> str | None:
        """First text reply or button press from our chat, or None on timeout."""
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                batch = self.updates(timeout=int(min(25, max(1, deadline - time.time()))))
            except TelegramError:
                time.sleep(3)  # network blip: keep waiting
                continue
            for u in batch:
                if not self._mine(u):
                    continue
                if "callback_query" in u:
                    cq = u["callback_query"]
                    msg = cq.get("message", {})
                    if after_message_id and msg.get("message_id") != after_message_id:
                        continue
                    try:
                        self._call("answerCallbackQuery", {"callback_query_id": cq["id"]})
                        self._call("editMessageReplyMarkup", {"chat_id": self.chat_id,
                                                              "message_id": msg.get("message_id"),
                                                              "reply_markup": {"inline_keyboard": []}})
                    except TelegramError:
                        pass
                    return cq.get("data", "")
                text = (u.get("message") or {}).get("text")
                if text:
                    return text.strip()
        return None


def _keyboard(rows: list[list[tuple[str, str]]]) -> dict:
    return {"inline_keyboard": [[{"text": label, "callback_data": data} for label, data in row] for row in rows]}


def _multipart(fields: dict, files: dict) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    buf = io.BytesIO()
    for k, v in fields.items():
        if v is None:
            continue
        buf.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n".encode())
        buf.write(str(v).encode())
        buf.write(b"\r\n")
    for k, (filename, data, ctype) in files.items():
        buf.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"; "
                  f"filename=\"{filename}\"\r\nContent-Type: {ctype}\r\n\r\n".encode())
        buf.write(data)
        buf.write(b"\r\n")
    buf.write(f"--{boundary}--\r\n".encode())
    return buf.getvalue(), f"multipart/form-data; boundary={boundary}"


def from_env() -> Telegram | None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    return Telegram(token, chat) if token and chat else None


def _setup() -> int:
    from ..config import ROOT  # noqa: F401  (loads .env)
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("Put TELEGRAM_BOT_TOKEN in .env first (from @BotFather).")
        return 1
    tg = Telegram(token)
    me = tg._call("getMe")
    print(f'Bot: @{me["username"]}. Send it any message in Telegram (e.g. "hi"), waiting 120s...')
    deadline = time.time() + 120
    while time.time() < deadline:
        for u in tg.updates(timeout=20):
            chat = (u.get("message") or {}).get("chat")
            if chat:
                name = chat.get("first_name") or chat.get("title") or ""
                print(f"Chat id: {chat['id']} ({name}). Put TELEGRAM_CHAT_ID={chat['id']} in .env")
                return 0
    print("No message arrived. Message the bot and run setup again.")
    return 1


def _test() -> int:
    from ..config import ROOT  # noqa: F401  (loads .env)
    tg = from_env()
    if tg is None:
        print("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env (run setup first).")
        return 1
    tg.drain()
    sent = tg.send_text("train-agent test: tap a button or type a reply.",
                        buttons=[[("Approve", "approve"), ("Reject", "reject")]])
    print("Sent. Waiting up to 120s for your answer...")
    answer = tg.wait_reply(120, after_message_id=sent["message_id"])
    print(f"Answer: {answer!r}")
    if answer:
        tg.send_text(f"Got it: {answer}")
    return 0 if answer else 1


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    sys.exit(_setup() if cmd == "setup" else _test() if cmd == "test" else print(__doc__) or 0)
