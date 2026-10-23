"""The agent daemon: starts runs from a chat command, a schedule, or an incoming email.

    python -m agent.serve                         # run the daemon (leave it running)
    python -m agent.serve add "10:59:30" "task"   # schedule a run (also: "tomorrow 10:45", "in 5m")
    python -m agent.serve add "08:00" "task" --daily
    python -m agent.serve list
    python -m agent.serve cancel <job id>

Telegram commands (only from your chat):
    /run <task>            start now (queued if busy)
    /at <when> <task>      schedule once: /at 10:59:30 ..., /at tomorrow 10:45 ..., /at in 5m ...
    /daily <HH:MM[:SS]> <task>
    /jobs                  list scheduled runs
    /cancel <id>           remove a scheduled run
    /stop                  stop the current run
    /status                what is running, queued, next job, clock sync
    /help

Runs execute one at a time on the main thread (the computer has one screen). While jobs are
pending the PC is kept awake, and the clock is synced to internet time for exact starts.
"""

from __future__ import annotations

import argparse
import ctypes
import queue
import sys
import threading
import time
from dataclasses import dataclass
from datetime import timedelta

from . import clock
from .channels.telegram import TelegramError, from_env
from .config import Settings, settings as default_settings
from .handoff import ConsoleHandoff, TelegramHandoff
from .triggers.email_watch import EmailWatcher
from .triggers.schedule import JobStore

EMAIL_POLL_S = 30
RESYNC_S = 1800

HELP = __doc__.split("Telegram commands (only from your chat):")[1].split("Runs execute")[0].strip()


@dataclass
class Request:
    task: str
    source: str  # chat | schedule:<id> | email:<rule>


def keep_awake(on: bool) -> None:
    """Stop Windows sleeping (and the screen turning off) while runs are pending."""
    if sys.platform != "win32":
        return
    ES_CONTINUOUS, ES_SYSTEM_REQUIRED, ES_DISPLAY_REQUIRED = 0x80000000, 0x00000001, 0x00000002
    flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED if on else 0)
    ctypes.windll.kernel32.SetThreadExecutionState(flags)


def split_when(text: str) -> tuple[str, str]:
    """'tomorrow 10:45 book it' -> ('tomorrow 10:45', 'book it'). Longest time prefix wins."""
    words = text.split()
    for n in (3, 2, 1):
        if len(words) > n:
            head = " ".join(words[:n])
            try:
                clock.parse_when(head)
                return head, " ".join(words[n:])
            except (ValueError, KeyError, IndexError):
                continue
    raise ValueError("Usage: /at <when> <task>, e.g. /at 10:59:30 book the ticket")


class Daemon:
    def __init__(self, settings: Settings = default_settings, use_telegram: bool = True, echo=print):
        self.settings = settings
        self.echo = echo
        self.store = JobStore(settings.data_dir / "schedule.json")
        self.requests: queue.Queue[Request] = queue.Queue()
        self.stopping = threading.Event()
        self.agent = None
        self.current: Request | None = None
        self.tg = from_env() if use_telegram else None
        if self.tg:
            self.tg.inbox = queue.Queue()
        self.email = EmailWatcher.from_env(settings.data_dir)

    # --- messaging --------------------------------------------------------
    def say(self, text: str) -> None:
        self.echo(f"[daemon] {text}")
        if self.tg:
            try:
                self.tg.send_text(text)
            except TelegramError as exc:
                self.echo(f"[daemon] telegram send failed: {exc}")

    def status(self) -> str:
        lines = [f"Clock: {clock.status()}", f"Now: {clock.now():%Y-%m-%d %H:%M:%S}"]
        lines.append(f"Running: {self.current.task[:200]} ({self.current.source})" if self.current else "Running: nothing")
        lines.append(f"Queued: {self.requests.qsize()}")
        jobs = self.store.list()
        lines.append(f"Next job: {jobs[0].line()}" if jobs else "Next job: none")
        lines.append(f"Email trigger: {'on (' + self.email.user + ')' if self.email else 'off'}")
        return "\n".join(lines)

    def submit(self, task: str, source: str) -> None:
        ahead = self.requests.qsize() + (1 if self.current else 0)
        self.requests.put(Request(task, source))
        self.say(f"Queued ({source}): {task[:200]}" + (f"\n{ahead} run(s) ahead." if ahead else ""))

    # --- chat commands ----------------------------------------------------
    def handle_command(self, text: str) -> None:
        cmd, _, rest = text.strip().partition(" ")
        cmd = cmd.split("@")[0].lower()
        rest = rest.strip()
        try:
            if cmd in ("/start", "/help"):
                self.say(HELP)
            elif cmd == "/run":
                if not rest:
                    raise ValueError("Usage: /run <task>")
                self.submit(rest, "chat")
            elif cmd == "/at":
                when_s, task = split_when(rest)
                job = self.store.add(task, clock.parse_when(when_s), source="telegram")
                self.say(f"Scheduled {job.line()}")
            elif cmd == "/daily":
                when_s, task = split_when(rest)
                job = self.store.add(task, clock.parse_when(when_s), repeat="daily", source="telegram")
                self.say(f"Scheduled {job.line()}")
            elif cmd == "/jobs":
                jobs = self.store.list()
                self.say("\n".join(j.line() for j in jobs) if jobs else "No scheduled runs.")
            elif cmd == "/cancel":
                self.say("Cancelled." if self.store.cancel(rest) else f"No job {rest!r}. See /jobs.")
            elif cmd == "/stop":
                if self.agent and self.current:
                    self.agent.cancel.set()
                    self.say("Stopping the current run after its current step.")
                else:
                    self.say("Nothing is running.")
            elif cmd == "/status":
                self.say(self.status())
            else:
                self.say(f"Unknown command {cmd}. /help lists them.")
        except ValueError as exc:
            self.say(str(exc))

    # --- background threads -------------------------------------------------
    def poll_telegram(self) -> None:
        while not self.stopping.is_set():
            try:
                updates = self.tg.updates(timeout=20)
            except TelegramError as exc:
                self.echo(f"[daemon] telegram poll failed: {exc}")
                time.sleep(5)
                continue
            for u in updates:
                try:
                    if not self.tg._mine(u):
                        continue
                    text = ((u.get("message") or {}).get("text") or "").strip()
                    self.echo(f"[daemon] telegram: {text[:80] or '(button)'}")
                    if text.startswith("/"):
                        self.handle_command(text)
                    elif self.tg.awaiting or "callback_query" in u:
                        self.tg.inbox.put(u)
                    elif text:
                        self.say("To start a run send /run <task>. /help lists all commands.")
                except Exception as exc:  # a bad message must not stop the poller
                    self.echo(f"[daemon] could not handle update: {type(exc).__name__}: {exc}")

    def run_scheduler(self) -> None:
        last_sync = time.time()
        while not self.stopping.is_set():
            if time.time() - last_sync > RESYNC_S:
                clock.sync()
                last_sync = time.time()
            nxt = self.store.next_due()
            keep_awake(bool(nxt) or self.current is not None or not self.requests.empty())
            if nxt is not None and (nxt - clock.now()) <= timedelta(seconds=5):
                clock.sleep_until(nxt, cancelled=self.stopping.is_set)
                run, missed = self.store.take_due()
                for job in run:
                    self.echo(f"[daemon] firing job {job.id} at {clock.now():%H:%M:%S.%f}")
                    self.submit(job.task, f"schedule:{job.id}")
                for job in missed:
                    self.say(f"Missed scheduled run (the daemon was not running in time): {job.line()}")
            else:
                self.stopping.wait(1.0)  # re-read the store each second: jobs added by CLI show up

    def watch_email(self) -> None:
        while not self.stopping.is_set():
            try:
                for task, info in self.email.poll():
                    self.submit(task, f"email:{info.get('rule') or 'rule'}")
            except Exception as exc:  # network, auth: keep the daemon alive
                self.echo(f"[daemon] email check failed: {type(exc).__name__}: {exc}")
            self.stopping.wait(EMAIL_POLL_S)

    # --- main loop -----------------------------------------------------------
    def serve(self) -> None:
        self.echo(f"[daemon] {clock.sync()}")
        from .loop import Agent  # created here: screen, UI Automation and browser live on this thread
        handoff = TelegramHandoff(self.tg, self.settings.handoff_timeout, self.echo) if self.tg else ConsoleHandoff()
        self.agent = Agent(self.settings, handoff=handoff, echo=self.echo)
        threads = [threading.Thread(target=self.run_scheduler, daemon=True, name="scheduler")]
        if self.tg:
            while self.tg.updates(timeout=0):  # skip messages sent while the daemon was down
                pass
            threads.append(threading.Thread(target=self.poll_telegram, daemon=True, name="telegram"))
        if self.email:
            threads.append(threading.Thread(target=self.watch_email, daemon=True, name="email"))
        for t in threads:
            t.start()
        self.say("\U0001F7E2 Agent daemon is up.\n" + self.status())
        try:
            while not self.stopping.is_set():
                try:
                    req = self.requests.get(timeout=0.5)
                except queue.Empty:
                    continue
                self.current = req
                self.echo(f"[daemon] starting run ({req.source}) at {clock.now():%H:%M:%S.%f}: {req.task}")
                try:
                    self.agent.run(req.task)
                except Exception as exc:  # one bad run must not take the daemon down
                    self.say(f"Run crashed: {type(exc).__name__}: {exc}")
                finally:
                    self.current = None
        except KeyboardInterrupt:
            pass
        finally:
            self.stopping.set()
            keep_awake(False)
            self.say("\U0001F534 Agent daemon stopped.")


def main() -> int:
    parser = argparse.ArgumentParser(prog="agent.serve", description="Agent daemon and schedule.")
    sub = parser.add_subparsers(dest="cmd")
    p_add = sub.add_parser("add", help="Schedule a run.")
    p_add.add_argument("when")
    p_add.add_argument("task", nargs="+")
    p_add.add_argument("--daily", action="store_true")
    sub.add_parser("list", help="List scheduled runs.")
    p_cancel = sub.add_parser("cancel", help="Cancel a scheduled run.")
    p_cancel.add_argument("job_id")
    parser.add_argument("--no-telegram", action="store_true", help="Ignore Telegram (console handoff).")
    args = parser.parse_args()

    store = JobStore(default_settings.data_dir / "schedule.json")
    if args.cmd == "add":
        print(clock.sync())
        job = store.add(" ".join(args.task), clock.parse_when(args.when), "daily" if args.daily else "")
        print(f"Scheduled {job.line()}\nThe daemon (python -m agent.serve) must be running at that time.")
        return 0
    if args.cmd == "list":
        jobs = store.list()
        print("\n".join(j.line() for j in jobs) if jobs else "No scheduled runs.")
        return 0
    if args.cmd == "cancel":
        print("Cancelled." if store.cancel(args.job_id) else "No such job.")
        return 0
    Daemon(use_telegram=not args.no_telegram).serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())
