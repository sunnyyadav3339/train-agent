"""Scheduled runs: one-shot ("at 10:59:30") or daily, saved to data/schedule.json.

The file survives restarts, so a run scheduled before code freeze still fires even if the
daemon is restarted. Jobs that were missed while nothing was running are reported, not run
late (a Tatkal booking at 11:20 is worse than none).
"""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from .. import clock

LATE_GRACE_S = 120  # a job up to this late still runs (e.g. daemon restarted at 10:59:45)


@dataclass
class Job:
    id: str
    task: str
    at: str                    # ISO local time of the next run
    repeat: str = ""           # "" (once) or "daily"
    source: str = "cli"        # cli | telegram
    created: str = field(default_factory=lambda: clock.now().isoformat(timespec="seconds"))

    @property
    def when(self) -> datetime:
        return datetime.fromisoformat(self.at)

    def line(self) -> str:
        rep = " daily" if self.repeat == "daily" else ""
        return f"{self.id} at {self.when:%Y-%m-%d %H:%M:%S}{rep}: {self.task[:120]}"


class JobStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock = threading.Lock()

    def _read(self) -> list[Job]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except ValueError:
            return []
        return [Job(**j) for j in raw]

    def _write(self, jobs: list[Job]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps([asdict(j) for j in jobs], indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def list(self) -> list[Job]:
        with self.lock:
            return sorted(self._read(), key=lambda j: j.at)

    def add(self, task: str, when: datetime, repeat: str = "", source: str = "cli") -> Job:
        if repeat not in ("", "daily"):
            raise ValueError("repeat must be '' or 'daily'")
        job = Job(id=uuid.uuid4().hex[:6], task=task, at=when.isoformat(timespec="milliseconds"),
                  repeat=repeat, source=source)
        with self.lock:
            jobs = self._read()
            jobs.append(job)
            self._write(jobs)
        return job

    def cancel(self, job_id: str) -> bool:
        with self.lock:
            jobs = self._read()
            keep = [j for j in jobs if j.id != job_id]
            self._write(keep)
            return len(keep) != len(jobs)

    def take_due(self, now: datetime | None = None) -> tuple[list[Job], list[Job]]:
        """Pop jobs that are due. Returns (to_run, missed). Daily jobs are moved to their next day."""
        now = now or clock.now()
        run, missed = [], []
        with self.lock:
            jobs = self._read()
            keep = []
            for j in jobs:
                if j.when > now:
                    keep.append(j)
                    continue
                late = (now - j.when).total_seconds()
                (run if late <= LATE_GRACE_S else missed).append(j)
                if j.repeat == "daily":
                    nxt = j.when
                    while nxt <= now:
                        nxt += timedelta(days=1)
                    keep.append(Job(j.id, j.task, nxt.isoformat(timespec="milliseconds"), j.repeat, j.source, j.created))
            if run or missed:
                self._write(keep)
        return run, missed

    def next_due(self) -> datetime | None:
        jobs = self.list()
        return jobs[0].when if jobs else None
