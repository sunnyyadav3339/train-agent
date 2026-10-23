"""Offline checks for hands and eyes (no API key needed): python -m tests.smoke_offline"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from agent import skills
from agent.channels.telegram import Telegram
from agent.handoff import TelegramHandoff
from tests.fake_telegram import FakeTelegram
from agent.config import settings
from agent.eyes.marks import Element, Observation, observe, open_windows
from agent.fastpath import match
from agent.eyes.screen import API_MAX_VISUAL_TOKENS, Screen, fit_scale
from agent.models import _strip_images
from agent.replay import build_frames, build_replay
from agent.runlog import RunLog
from agent.tools import TOOLS
from agent.tools.browser import pw_combo
from agent.tools.desktop import parse_combo
from agent.tools.fs import list_dir, read_file, write_file
from agent.tools.shell import run_shell


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {label}{' - ' + detail if detail else ''}")
    return ok


def main() -> int:
    results = []
    screen = Screen(settings.max_long_edge)
    results.append(check("screen size", screen.width > 0,
                         f"{screen.width}x{screen.height} -> model {screen.model_width}x{screen.model_height}"))
    img = screen.capture()
    tokens = -(-img.width // 28) * -(-img.height // 28)
    results.append(check("screenshot fits limits", img.size == (screen.model_width, screen.model_height)
                         and tokens <= API_MAX_VISUAL_TOKENS, f"{img.size}"))
    mx, my = screen.model_width // 2, screen.model_height // 2
    back = screen.to_model(*screen.to_screen(mx, my))
    results.append(check("coordinate round trip", abs(back[0] - mx) <= 1 and abs(back[1] - my) <= 1, f"{(mx, my)} -> {back}"))
    results.append(check("4K scale", fit_scale(3840, 2160, 1920) == 0.5))
    results.append(check("zoom crop", screen.crop([0, 0, 200, 100]).width > 0))

    results.append(check("key combos", parse_combo("ctrl+s") == ["ctrl", "s"] and parse_combo("Return") == ["enter"]
                         and parse_combo("alt+Tab") == ["alt", "tab"] and parse_combo("super") == ["win"]))
    results.append(check("browser key combos", pw_combo("ctrl+a") == "Control+a" and pw_combo("Return") == "Enter"
                         and pw_combo("shift+tab") == "Shift+Tab" and pw_combo("esc") == "Escape"))

    obs = observe(screen, settings.max_elements)
    results.append(check("numbered elements", len(obs.elements) > 0 and min(obs.elements) == 1,
                         f'{len(obs.elements)} in "{obs.window[:40]}"'))
    results.append(check("annotated screenshot", obs.image.size == img.size))
    results.append(check("open windows", len(open_windows()) > 0))

    out = run_shell("Write-Output hello-agent", 30)
    results.append(check("shell", "hello-agent" in out and "exit_code: 0" in out))
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "sub" / "a.txt"
        write_file(str(p), "line1\n")
        write_file(str(p), "line2\n", append=True)
        results.append(check("file write/append/read", read_file(str(p)) == "line1\nline2\n"))
        results.append(check("list_dir", "[dir] sub" in list_dir(d)))

    names = [t["function"]["name"] for t in TOOLS]
    results.append(check("tool schemas", all("reason" in t["function"]["parameters"]["required"] for t in TOOLS)
                         and len(set(names)) == len(names) and json.dumps(TOOLS) != "", f"{len(names)} tools"))
    msgs = [{"role": "user", "content": [{"type": "text", "text": "x"}, {"type": "image_url", "image_url": {"url": "d"}}]}]
    stripped = _strip_images(msgs)[0]["content"]
    results.append(check("text-only fallback strips images", all(p["type"] == "text" for p in stripped)))

    with tempfile.TemporaryDirectory() as d:
        log = RunLog(Path(d), "replay test")
        log.event("task", task="replay test", screenshot=log.save_image(img, "start"))
        log.event("model", step=1, route="gemini/x", latency_s=1.0, text="", calls=1)
        log.event("action", step=1, tool="click", input={"id": 3}, reason="open menu", result="Clicked")
        log.event("observe", step=1, screenshot=log.save_image(img, "s1_obs"), window="W", elements=5)
        log.event("route_failed", route="gemini/x", status=429, exhausted=None, detail="slow down")
        log.event("model", step=2, route="groq/y", latency_s=0.5, text="", calls=1)
        log.event("action", step=2, tool="key", input={"combo": "</script>"}, reason="r", error="Error: boom")
        log.close("failed", "test <b>summary</b>")
        page = build_replay(log.dir).read_text(encoding="utf-8")
        frames, stats = build_frames(log.dir, [json.loads(l) for l in (log.dir / "log.jsonl").open(encoding="utf-8")], False)
        results.append(check("replay page", page.count("</script>") == 2 and "data:image/jpeg" in page
                             and len(frames) == 2 and stats["errors"] == 1 and stats["model_switches"] == 1
                             and frames[1]["notes"][0]["kind"] == "route_failed" and frames[0]["after"] is not None))

    loaded = skills.load_all(settings.skills_dir)
    results.append(check("skills load", all(s.description for s in loaded.values()), f"{list(loaded)}"))
    results.append(check("skill selection", skills.select(loaded, "open notepad and save a note") is not None
                         and skills.select(loaded, "zzz qqq") is None))
    rendered = skills.render({"a": "{p|basename}-{today+1:%Y}", "b": "{{x}}"}, {"p": r"C:\d\f.txt"})
    results.append(check("skill templating", rendered["a"].startswith("f.txt-") and rendered["b"] == "{x}"))
    fake = Observation(image=img, raw=img, elements={
        1: Element(1, "Button", "Save", "", (0, 0, 10, 10)),
        2: Element(2, "input:radio", "Large", "checked", (0, 20, 10, 30))}, window="W", windows=[])
    results.append(check("fast-path target match", match(fake, {"role": "button", "name": "save"}).id == 1
                         and match(fake, {"role": "input", "value": "checked"}).id == 2
                         and match(fake, {"name": "nope"}) is None))

    results.extend(handoff_checks(img))
    results.extend(trigger_checks())

    print(f"\n{sum(results)}/{len(results)} passed")


def trigger_checks() -> list[bool]:
    from datetime import datetime, timedelta
    from email.message import EmailMessage

    from agent import clock
    from agent.serve import Daemon, split_when
    from agent.triggers.email_watch import _body_text, matches
    from agent.triggers.schedule import JobStore

    out = []
    base = datetime(2026, 10, 25, 10, 0, 0)
    out.append(check("parse times", clock.parse_when("10:59:30", base) == datetime(2026, 10, 25, 10, 59, 30)
                     and clock.parse_when("09:00", base) == datetime(2026, 10, 26, 9, 0)
                     and clock.parse_when("tomorrow 10:45", base) == datetime(2026, 10, 26, 10, 45)
                     and clock.parse_when("in 5m", base) == base + timedelta(minutes=5)
                     and clock.parse_when("2026-10-25 11:00:00", base) == datetime(2026, 10, 25, 11, 0)))
    out.append(check("split /at text", split_when("tomorrow 10:45 book it") == ("tomorrow 10:45", "book it")
                     and split_when("10:59:30 go") == ("10:59:30", "go")))
    with tempfile.TemporaryDirectory() as d:
        store = JobStore(Path(d) / "s.json")
        now = datetime(2026, 10, 25, 11, 0, 0)
        store.add("due", now - timedelta(seconds=1))
        store.add("missed", now - timedelta(minutes=30))
        daily = store.add("daily", now - timedelta(seconds=2), repeat="daily")
        store.add("later", now + timedelta(hours=1))
        run, missed = store.take_due(now)
        left = {j.task: j for j in store.list()}
        out.append(check("schedule due/missed/daily",
                         sorted(j.task for j in run) == ["daily", "due"] and [j.task for j in missed] == ["missed"]
                         and set(left) == {"daily", "later"} and left["daily"].when.date() == datetime(2026, 10, 26).date()
                         and store.cancel(daily.id) and len(store.list()) == 1))

    msg = EmailMessage()
    msg["From"], msg["Subject"] = "Me <me@x.com>", "agent: open notepad"
    msg.set_content("type hello please")
    out.append(check("email rule + body", matches({"from": "me@x.com", "subject": "agent:"}, msg["From"], msg["Subject"])
                     and not matches({"from": "boss@x.com"}, msg["From"], msg["Subject"])
                     and _body_text(msg) == "type hello please"))

    fake = FakeTelegram(chat_id=42)
    try:
        with tempfile.TemporaryDirectory() as d:
            dm = Daemon.__new__(Daemon)
            dm.settings, dm.echo = settings, (lambda *a: None)
            dm.store, dm.requests = JobStore(Path(d) / "s.json"), __import__("queue").Queue()
            dm.tg, dm.email, dm.agent, dm.current = Telegram("T", 42, api=fake.url), None, None, None
            dm.handle_command("/at in 10m say hi")
            dm.handle_command("/daily 08:00:00 morning check")
            jobs = dm.store.list()
            dm.handle_command(f"/cancel {jobs[0].id}")
            dm.handle_command("/run open notepad")
            dm.handle_command("/status")
            texts = [p.get("text", "") for m, p in fake.sent if m == "sendMessage"]
            out.append(check("telegram commands", len(jobs) == 2 and len(dm.store.list()) == 1
                             and dm.requests.get_nowait().task == "open notepad"
                             and any(t.startswith("Clock:") for t in texts)))
    finally:
        fake.close()
    return out


def handoff_checks(img) -> list[bool]:
    """Telegram handoff against a fake Bot API: buttons, typed replies, other chats, timeouts."""
    fake = FakeTelegram(chat_id=42)
    tg = Telegram("TEST", 42, api=fake.url)
    h = TelegramHandoff(tg, timeout_s=4, echo=lambda *a: None)
    out = []
    try:
        fake.push_text("stale message from before the question")
        fake.on_send = lambda m, p, mid: (fake.push_text("intruder says yes", chat_id=999),
                                          fake.push_button("approved", mid)) if m == "sendPhoto" else None
        ans = h.ask("Pay Rs 145 for the ticket?", "approval", img)
        photo = [p for m, p in fake.sent if m == "sendPhoto"][0]
        out.append(check("handoff approval button", ans == "approved" and photo["has_markup"]
                         and any(m == "answerCallbackQuery" for m, _ in fake.sent)))
        fake.on_send = lambda m, p, mid: fake.push_text("Use the 11:00 train") if m == "sendPhoto" else None
        out.append(check("handoff typed reply", h.ask("Which train?", "judgement", img) == "Use the 11:00 train"))
        fake.on_send = None
        sent_before = len(fake.sent)
        ans = h.ask("Anyone there?", "stuck", None)
        reminded = any("Still waiting" in p.get("text", "") for m, p in fake.sent[sent_before:])
        out.append(check("handoff timeout + reminder", ans.startswith("(no answer") and reminded))
        h.notify("run finished", image=img)
        out.append(check("notify with screenshot", fake.sent[-1][0] == "sendPhoto"))
    finally:
        fake.close()
    return out
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
