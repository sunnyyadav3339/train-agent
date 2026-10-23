"""Human handoff: pause, ask a person, resume.

Phase 1 asks in the console. Phase 5 adds a Telegram channel with the same interface.
"""

from __future__ import annotations

from typing import Protocol

from PIL import Image


class Handoff(Protocol):
    def ask(self, question: str, kind: str, screenshot: Image.Image | None) -> str: ...


class ConsoleHandoff:
    def ask(self, question: str, kind: str, screenshot: Image.Image | None) -> str:
        print(f"\n=== AGENT NEEDS YOU ({kind}) ===\n{question}")
        answer = input("Your answer> ").strip()
        return answer or "(no answer)"
