"""The loop: plan -> act -> observe -> verify -> repeat, until done or the human is needed.

The harness is task-agnostic. Each turn the model gets an observation (numbered elements
from the UI structure, plus the screenshot with those numbers drawn on it), chooses actions,
the harness runs them, and the next observation shows what changed so the model can verify.
Every action is logged with its reason and the screenshot it led to.
"""

from __future__ import annotations

import base64
import io
import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime

from PIL import Image

from . import prompts
from .config import Settings, settings as default_settings
from .eyes.marks import Observation, foreground_title, observe
from .eyes.screen import Screen, png_bytes
from .handoff import ConsoleHandoff, Handoff
from .models import ModelRouter
from .replay import build_replay
from .runlog import RunLog
from .tools import SCREEN_CHANGING, TOOLS
from .tools import desktop
from .tools.browser import Browser
from .tools.fs import list_dir, read_file, write_file
from .tools.shell import run_shell

SKIPPED = "Not executed: an earlier action in this turn failed."
INPUT_TOOLS = {"click", "click_xy", "type_text", "key", "scroll", "drag"}
MAX_NUDGES = 2


@dataclass
class RunResult:
    status: str  # success | failed | gave_up | error
    summary: str
    run_dir: str
    steps: int


def _image_part(img: Image.Image) -> dict:
    data = base64.standard_b64encode(png_bytes(img)).decode()
    return {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}"}}


OBS_TAG = "__observation__"


def _compact_old_observations(messages: list) -> None:
    """Shrink earlier observations to one line each.

    Old element lists and screenshots are stale (ids change every turn) and are most of the
    tokens; keeping only the newest one keeps each request small enough for free tiers.
    """
    for m in messages:
        if m.get(OBS_TAG) and isinstance(m.get("content"), list):
            m["content"] = m[OBS_TAG]


class Agent:
    def __init__(self, settings: Settings = default_settings, handoff: Handoff | None = None, echo=print):
        self.settings = settings
        self.handoff = handoff or ConsoleHandoff()
        self.echo = echo
        self.screen = Screen(settings.max_long_edge)
        self.log: RunLog | None = None
        self.router = ModelRouter(settings, on_event=self._event)
        self.browser = Browser(settings.chrome_profile_dir, settings.downloads_dir, settings.browser_channel)
        self.obs: Observation | None = None
        self._obs_page = None
        self.pending_images: list[Image.Image] = []

    def _event(self, kind: str, **data) -> None:
        if self.log:
            self.log.event(kind, **data)
        self.echo(f"[{kind}] {data}")

    # ------------------------------------------------------------------
    def run(self, task: str, system_extra: str = "") -> RunResult:
        s = self.settings
        self.log = RunLog(s.runs_dir, task, meta={
            "routes": self.router.describe(),
            "screen": [self.screen.width, self.screen.height],
            "model_space": [self.screen.model_width, self.screen.model_height],
        })
        self.echo("Models: " + "; ".join(self.router.describe()))
        system = prompts.SYSTEM + (f"\n{system_extra}" if system_extra else "")
        messages: list = [
            {"role": "system", "content": system},
            {"role": "user", "content": [{"type": "text", "text": prompts.task_message(
                task, self.screen.model_width, self.screen.model_height,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"))}]},
        ]
        self._observe_into(messages, step=0, settle=False)

        nudges = 0
        for step in range(1, s.max_steps + 1):
            t0 = time.time()
            try:
                msg, route = self.router.chat(messages, TOOLS)
            except Exception as exc:
                return self._finish("error", f"Model call failed: {exc}", step)

            calls = list(msg.tool_calls or [])
            assistant = {"role": "assistant", "content": msg.content or ""}
            if calls:
                dumped = []
                for c in calls:
                    d = c.model_dump(exclude_none=True)  # keeps provider extras (e.g. Gemini signatures)
                    d.setdefault("id", f"call_{uuid.uuid4().hex[:12]}")
                    d.setdefault("type", "function")
                    dumped.append(d)
                assistant["tool_calls"] = dumped
            messages.append(assistant)
            self._event("model", step=step, route=route, latency_s=round(time.time() - t0, 2),
                        text=(msg.content or "")[:500], calls=len(calls))

            if not calls:
                if nudges >= MAX_NUDGES:
                    return self._finish("gave_up", msg.content or "Model stopped without finishing.", step)
                nudges += 1
                messages.append({"role": "user", "content": (
                    "Continue the task by calling a tool. If it is done and verified, call task_complete; "
                    "if you cannot proceed, call ask_human.")})
                continue
            nudges = 0

            changed, finished = self._execute(step, assistant["tool_calls"], messages)
            if finished:
                return self._finish(finished.get("status", "success"), finished.get("summary", ""),
                                    step, finished.get("evidence"))
            self._observe_into(messages, step, settle=changed)

        return self._finish("gave_up", f"Step limit ({s.max_steps}) reached.", s.max_steps)

    # ------------------------------------------------------------------
    def _observe_into(self, messages: list, step: int, settle: bool) -> None:
        if settle:
            time.sleep(self.settings.settle_seconds)
        t0 = time.time()
        self.obs = observe(self.screen, self.settings.max_elements, self.browser)
        self._obs_page = self.browser.page if self.obs.mode == "browser" else None
        shot = self.log.save_image(self.obs.image, f"s{step}_obs")
        self.log.event("observe", step=step, screenshot=shot, window=self.obs.window,
                       mode=self.obs.mode, url=self.obs.url or None, notes=self.obs.notes or None,
                       elements=len(self.obs.elements), dur_s=round(time.time() - t0, 2))
        _compact_old_observations(messages)
        parts: list = [{"type": "text", "text": f"Observation after step {step}:\n{self.obs.text()}"}]
        for img in self.pending_images:
            parts += [{"type": "text", "text": "Zoomed crop:"}, _image_part(img)]
        self.pending_images = []
        parts.append(_image_part(self.obs.image))
        summary = (f"[Observation after step {step}: active window \"{self.obs.window}\", "
                   f"{len(self.obs.elements)} elements. Superseded by later observations.]")
        messages.append({"role": "user", "content": parts, OBS_TAG: summary})

    def _execute(self, step: int, calls: list, messages: list) -> tuple[bool, dict | None]:
        failed = False
        changed = False
        for c in calls:
            name = c["function"]["name"]
            raw_args = c["function"].get("arguments") or "{}"
            if failed:
                messages.append({"role": "tool", "tool_call_id": c["id"], "content": SKIPPED})
                self.log.event("action", step=step, tool=name, skipped=True)
                continue
            t0 = time.time()
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                reason = args.pop("reason", "")
                if name == "task_complete":
                    self.log.event("action", step=step, tool=name, input=args, reason=reason)
                    return changed, args
                out = self._dispatch(name, args)
                messages.append({"role": "tool", "tool_call_id": c["id"], "content": out})
                self.log.event("action", step=step, tool=name, input=args, reason=reason,
                               result=out[:2000], dur_s=round(time.time() - t0, 2))
                changed = changed or name in SCREEN_CHANGING
                shown = {k: v for k, v in args.items() if k != "text"} if name == "type_text" else args
                self.echo(f"  step {step}: {name} {shown}  # {reason}")
            except Exception as exc:
                failed = True
                err = f"Error: {type(exc).__name__}: {exc}"
                messages.append({"role": "tool", "tool_call_id": c["id"], "content": err})
                self.log.event("action", step=step, tool=name, raw_args=str(raw_args)[:2000], error=err)
                self.echo(f"  step {step}: {name} FAILED {err}")
        return changed, None

    # ------------------------------------------------------------------
    def _element_point(self, el_id) -> tuple[float, float]:
        el = self.obs.elements.get(int(el_id)) if self.obs else None
        if el is None:
            raise LookupError(f"No element with id {el_id} in the latest observation.")
        return el.center

    def _xy(self, x, y) -> tuple[float, float]:
        """Screenshot (model) coordinates -> surface pixels of the observed screen or page."""
        return self.obs.to_surface(float(x), float(y))

    @property
    def _hands(self):
        """Input goes into the page while the browser is observed, else to the desktop."""
        return self.browser if self.obs and self.obs.mode == "browser" else desktop

    def _guard_foreground(self) -> None:
        """Refuse to click or type if the target changed since the last observation.

        Element ids and coordinates belong to the observed window (or tab); acting on a
        different one sends input to the wrong place (e.g. typing into a chat window).
        """
        if self.obs and self.obs.mode == "browser":
            if not self.browser.running or self.browser.page is not self._obs_page:
                raise RuntimeError("The active browser tab changed since the last observation. "
                                   "Nothing was done; act on the next observation.")
            return
        expected = self.obs.window if self.obs else ""
        now = foreground_title()
        if expected and now != expected:
            raise RuntimeError(
                f'Foreground window changed from "{expected}" to "{now}" since the last observation. '
                f"Nothing was done. Use focus_window to bring the right window back, then act on the "
                f"next observation.")

    def _dispatch(self, name: str, a: dict) -> str:
        s = self.settings
        if name in INPUT_TOOLS:
            self._guard_foreground()
        hands = self._hands
        if name == "click":
            x, y = self._element_point(a["id"])
            return hands.click(x, y, a.get("button", "left"), int(a.get("clicks", 1)), a.get("modifiers"))
        if name == "click_xy":
            x, y = self._xy(a["x"], a["y"])
            return hands.click(x, y, a.get("button", "left"), int(a.get("clicks", 1)), a.get("modifiers"))
        if name == "type_text":
            if a.get("id") is not None:
                hands.click(*self._element_point(a["id"]))
            if a.get("clear"):
                hands.key("ctrl+a")
            out = hands.type_text(a["text"])
            if a.get("submit"):
                hands.key("enter")
                out += " and pressed Enter"
            return out
        if name == "key":
            return hands.key(a["combo"], int(a.get("repeat", 1)))
        if name == "scroll":
            if a.get("id") is not None:
                x, y = self._element_point(a["id"])
            elif a.get("x") is not None and a.get("y") is not None:
                x, y = self._xy(a["x"], a["y"])
            else:
                x = y = None
            return hands.scroll(a["direction"], int(a.get("amount", 5)), x, y)
        if name == "drag":
            return hands.drag(*self._xy(a["x0"], a["y0"]), *self._xy(a["x1"], a["y1"]))
        if name == "zoom":
            self.pending_images.append(self._zoom(a))
            return "Zoomed crop attached to the next observation."
        if name == "browser_open":
            return self.browser.open(a["url"], bool(a.get("new_tab")))
        if name == "browser_back":
            self.browser.ensure()
            return self.browser.back()
        if name == "browser_switch_tab":
            self.browser.ensure()
            return self.browser.switch_tab(int(a["index"]))
        if name == "browser_close_tab":
            self.browser.ensure()
            return self.browser.close_tab()
        if name == "read_page":
            if not self.browser.running:
                raise RuntimeError("The browser is not open. Use browser_open first.")
            return self.browser.read_text(int(a.get("max_chars", 8000)))
        if name == "wait":
            secs = min(float(a.get("seconds", 1)), 60)
            time.sleep(secs)
            return f"Waited {secs}s"
        if name == "focus_window":
            return desktop.focus_window(a["title"])
        if name == "shell":
            return run_shell(a["command"], s.shell_timeout, a.get("cwd"))
        if name == "read_file":
            return read_file(a["path"])
        if name == "write_file":
            return write_file(a["path"], a["content"], bool(a.get("append")))
        if name == "list_dir":
            return list_dir(a["path"])
        if name == "ask_human":
            shot = self.screen.capture()
            self.log.event("handoff", question=a["question"], handoff_kind=a.get("kind"),
                           screenshot=self.log.save_image(shot, "handoff"))
            answer = self.handoff.ask(a["question"], a.get("kind", "stuck"), shot)
            self.log.event("handoff_answer", answer=answer)
            return f"Human answered: {answer}"
        raise ValueError(f"Unknown tool: {name}")

    def _zoom(self, a: dict) -> Image.Image:
        if self.obs and self.obs.mode == "browser":
            x0, y0 = self._xy(a["x0"], a["y0"])
            x1, y1 = self._xy(a["x1"], a["y1"])
            png = self.browser.screenshot_png(clip={"x": x0, "y": y0, "width": max(1, x1 - x0),
                                                    "height": max(1, y1 - y0)})
            return Image.open(io.BytesIO(png)).convert("RGB")
        return self.screen.crop([a["x0"], a["y0"], a["x1"], a["y1"]])

    def _finish(self, status: str, summary: str, steps: int, evidence: str | None = None) -> RunResult:
        final = self.screen.capture()
        self.log.event("final", screenshot=self.log.save_image(final, "final"), evidence=evidence)
        self.log.close(status, summary)
        self.browser.close()
        self.echo(f"\n== {status.upper()} after {steps} steps: {summary}\n   run log: {self.log.dir}")
        try:
            self.echo(f"   replay:  {build_replay(self.log.dir)}")
        except Exception as exc:  # the replay page must never cost us the run result
            self.echo(f"   replay failed: {exc!r}")
        return RunResult(status, summary, str(self.log.dir), steps)
