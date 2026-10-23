"""Run log: every step saved with a screenshot and the reason for the action."""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from PIL import Image


class RunLog:
    def __init__(self, runs_dir: Path, task: str, meta: dict | None = None):
        self.run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.dir = runs_dir / self.run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self._log = (self.dir / "log.jsonl").open("a", encoding="utf-8")
        self._images = 0
        self.started = time.time()
        (self.dir / "meta.json").write_text(
            json.dumps({"run_id": self.run_id, "task": task, "started": datetime.now().isoformat(), **(meta or {})},
                       indent=2),
            encoding="utf-8",
        )

    def save_image(self, img: Image.Image, label: str) -> str:
        self._images += 1
        name = f"{self._images:04d}_{label}.png"
        img.save(self.dir / name, format="PNG", optimize=True)
        return name

    def event(self, kind: str, **data) -> None:
        record = {"ts": datetime.now().isoformat(timespec="milliseconds"),
                  "t": round(time.time() - self.started, 3), "kind": kind, **data}
        self._log.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        self._log.flush()

    def close(self, status: str, summary: str) -> None:
        self.event("run_end", status=status, summary=summary)
        meta_path = self.dir / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta.update(status=status, summary=summary, ended=datetime.now().isoformat(),
                    duration_s=round(time.time() - self.started, 1))
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        self._log.close()
