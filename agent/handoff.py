"""Human handoff: pause, ask a person through the agent's own channel, resume.

Telegram when TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set, otherwise the console.
Approvals get Approve / Reject buttons; other questions take a typed reply. If nobody
answers, the agent is told so and decides what to do (it never assumes a yes).
"""

from __future__ import annotations

import os
from typing import Protocol

from PIL import Image

from .channels.telegram import Telegram, TelegramError, from_env
from .eyes.screen import png_bytes

ICONS = {"approval": "\U0001F510", "judgement": "\U0001F914", "stuck": "\U0001F6A7"}


class Handoff(Protocol):
    def ask(self, question: str, kind: str, screenshot: Image.Image | None) -> str: ...


class Notifier(Protocol):
    def notify(self, text: str, image: Image.Image | None = None, document: str | None = None) -> None: ...


class ConsoleHandoff:
    def ask(self, question: str, kind: str, screenshot: Image.Image | None) -> str:
        print(f"\n=== AGENT NEEDS YOU ({kind}) ===\n{question}")
        if kind == "approval":
            print("Type 'approve' or 'reject'.")
        answer = input("Your answer> ").strip()
        return answer or "(no answer)"

    def notify(self, text: str, image: Image.Image | None = None, document: str | None = None) -> None:
        print(f"[notify] {text}")


class TelegramHandoff:
    def __init__(self, tg: Telegram, timeout_s: float, echo=print):
        self.tg = tg
        self.timeout_s = timeout_s
        self.echo = echo
        self.console = ConsoleHandoff()

    def ask(self, question: str, kind: str, screenshot: Image.Image | None) -> str:
        icon = ICONS.get(kind, "❓")
        how = "Tap a button." if kind == "approval" else "Reply with a message."
        caption = f"{icon} Agent needs you ({kind})\n\n{question}\n\n{how}"
        buttons = ([[("✅ Approve", "approved"), ("❌ Reject", "rejected")]] if kind == "approval"
                   else [[("Skip / no answer", "skipped")]])
        self.echo(f"\n=== Asking on Telegram ({kind}): {question}")
        try:
            self.tg.drain()
            sent = (self.tg.send_photo(png_bytes(screenshot), caption, buttons) if screenshot is not None
                    else self.tg.send_text(caption, buttons))
        except TelegramError as exc:
            self.echo(f"Telegram unavailable ({exc}); asking in the console instead.")
            return self.console.ask(question, kind, screenshot)
        msg_id = sent.get("message_id")
        answer = self.tg.wait_reply(self.timeout_s / 2, after_message_id=msg_id)
        if answer is None:
            try:
                self.tg.send_text(f"⏰ Still waiting for your answer ({kind}): {question[:300]}")
            except TelegramError:
                pass
            answer = self.tg.wait_reply(self.timeout_s / 2, after_message_id=msg_id)
        if answer is None:
            mins = round(self.timeout_s / 60, 1)
            return f"(no answer within {mins} minutes; do not assume approval)"
        try:
            self.tg.send_text(f"Got it: {answer}. Resuming.")
        except TelegramError:
            pass
        return answer

    def notify(self, text: str, image: Image.Image | None = None, document: str | None = None) -> None:
        try:
            if image is not None:
                self.tg.send_photo(png_bytes(image), text)
            else:
                self.tg.send_text(text)
            if document:
                self.tg.send_document(document, os.path.basename(document))
        except (TelegramError, OSError) as exc:
            self.echo(f"[notify failed] {exc}")


def make_handoff(timeout_s: float, echo=print):
    tg = from_env()
    return TelegramHandoff(tg, timeout_s, echo) if tg else ConsoleHandoff()
