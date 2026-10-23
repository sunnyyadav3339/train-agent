"""Fast path: replay a skill's steps.yaml with no model calls, checking every step.

A step either acts or waits:

    - do: click                 # any action tool: click, type_text, key, scroll, shell, browser_open, ...
      target: {role: button, name: "Search"}   # resolved to an element id on the current screen
      note: press Search
      expect: {url: "results"}  # checked after the action (polls until timeout)
      timeout: 10
      retries: 1                # repeat the action if expect fails (default 0)
      optional: true            # failure does not stop the run
      if: {element: {name: "Accept cookies"}}  # skip the step unless this holds now

    - wait_for: {window: "Notepad"}
      timeout: 15

Conditions (all listed must hold): window / url / text (substring, or a list = any of),
element / no_element (a target spec). Targets match role (prefix), name, value
(case-insensitive substrings) and take the `index`-th match (default 0).

If a required step fails, the runner stops and reports where, and the model carries on
from the current screen. Values like {text} or {today+1:%d-%m-%Y} are filled from params.
"""

from __future__ import annotations

import time

from .eyes.marks import Element, Observation
from .skills import Skill, render

ACTIONS = {"click", "click_xy", "type_text", "key", "scroll", "drag", "wait", "wait_until", "focus_window", "shell",
           "browser_open", "browser_back", "browser_switch_tab", "browser_close_tab", "read_page",
           "write_file", "read_file", "list_dir"}
DEFAULT_TIMEOUT = 10.0
POLL_S = 0.5


class StepFailed(Exception):
    pass


def _as_list(v) -> list[str]:
    return [str(x) for x in v] if isinstance(v, list) else [str(v)]


def match(obs: Observation, spec: dict) -> Element | None:
    def ok(e: Element) -> bool:
        if "role" in spec and not e.role.lower().startswith(str(spec["role"]).lower()):
            return False
        if "name" in spec and not any(n.lower() in e.name.lower() for n in _as_list(spec["name"])):
            return False
        if "value" in spec and not any(v.lower() in e.value.lower() for v in _as_list(spec["value"])):
            return False
        return True

    hits = [e for e in obs.elements.values() if ok(e)]
    idx = int(spec.get("index", 0))
    return hits[idx] if len(hits) > idx else None


class FastPath:
    def __init__(self, agent):
        self.agent = agent

    # --- observing ----------------------------------------------------------
    def _look(self) -> Observation:
        a = self.agent
        from .eyes.marks import observe
        a.obs = observe(a.screen, a.settings.max_elements, a.browser)
        a._obs_page = a.browser.page if a.obs.mode == "browser" else None
        return a.obs

    def _page_text(self, obs: Observation) -> str:
        if obs.mode == "browser":
            try:
                return self.agent.browser.read_text(50000)
            except Exception:
                pass
        return "\n".join(f"{e.name} {e.value}" for e in obs.elements.values())

    def holds(self, cond: dict, obs: Observation) -> tuple[bool, str]:
        if "window" in cond and not any(w.lower() in obs.window.lower() for w in _as_list(cond["window"])):
            return False, f'window is "{obs.window}", wanted {cond["window"]!r}'
        if "url" in cond and not any(u.lower() in obs.url.lower() for u in _as_list(cond["url"])):
            return False, f'url is "{obs.url}", wanted {cond["url"]!r}'
        if "text" in cond:
            text = self._page_text(obs).lower()
            if not any(t.lower() in text for t in _as_list(cond["text"])):
                return False, f"text {cond['text']!r} not on screen"
        if "element" in cond and match(obs, cond["element"]) is None:
            return False, f"no element matching {cond['element']}"
        if "no_element" in cond and match(obs, cond["no_element"]) is not None:
            return False, f"element {cond['no_element']} still present"
        return True, ""

    def wait_until(self, cond: dict, timeout: float) -> tuple[bool, str, Observation]:
        deadline = time.time() + timeout
        while True:
            obs = self._look()
            ok, why = self.holds(cond, obs)
            if ok or time.time() >= deadline:
                return ok, why, obs
            time.sleep(POLL_S)

    # --- running ------------------------------------------------------------
    def run(self, skill: Skill, params: dict, step_no: int, start_at: int = 1) -> str:
        a = self.agent
        missing = [p for p in skill.params if p not in params]
        if missing:
            return f"Not started: missing params {missing}. Pass them in params."
        ctx = {**a.data_context(), **params}
        done: list[str] = []
        steps = skill.steps
        for i, raw in enumerate(steps, start=1):
            if i < start_at:
                continue
            step = render(raw, ctx)
            label = f"{skill.name} step {i}/{len(steps)}" + (f": {step['note']}" if step.get("note") else "")
            t0 = time.time()
            try:
                if "if" in step:
                    ok, _ = self.holds(step["if"], self._look())
                    if not ok:
                        a.log.event("skill_step", step=step_no, skill=skill.name, index=i, note=step.get("note"),
                                    result="skipped (condition false)")
                        continue
                if "wait_for" in step:
                    ok, why, _ = self.wait_until(step["wait_for"], float(step.get("timeout", DEFAULT_TIMEOUT)))
                    if not ok:
                        raise StepFailed(why)
                    result = "condition met"
                elif "do" in step:
                    result = self._do(step, step_no, label)
                else:
                    raise StepFailed("step has neither 'do' nor 'wait_for'")
                done.append(f"{i}")
                a.log.event("skill_step", step=step_no, skill=skill.name, index=i, note=step.get("note"),
                            result=result[:500], dur_s=round(time.time() - t0, 2))
                a.echo(f"  fast path {label} -> {result[:80]}")
            except Exception as exc:
                why = str(exc) if isinstance(exc, StepFailed) else f"{type(exc).__name__}: {exc}"
                a.log.event("skill_step", step=step_no, skill=skill.name, index=i, note=step.get("note"),
                            error=why[:500], dur_s=round(time.time() - t0, 2))
                a.echo(f"  fast path {label} FAILED: {why[:120]}")
                if step.get("optional"):
                    continue
                return (f"Fast path stopped at step {i} of {len(steps)} ({step.get('note') or step}): {why}. "
                        f"Steps done: {', '.join(done) or 'none'}. Continue the task yourself from the "
                        f"current screen (or call run_skill_steps again with start_at once fixed).")
        return f"Fast path finished all {len(steps)} steps of {skill.name}. Verify the result on screen."

    def _do(self, step: dict, step_no: int, label: str) -> str:
        a = self.agent
        tool = step["do"]
        if tool not in ACTIONS:
            raise StepFailed(f"unknown action {tool!r}")
        args = {k: v for k, v in step.items()
                if k not in ("do", "target", "note", "expect", "timeout", "retries", "optional", "if")}
        retries = int(step.get("retries", 0))
        timeout = float(step.get("timeout", DEFAULT_TIMEOUT))
        last_why = ""
        for attempt in range(retries + 1):
            if "target" in step:
                obs = a.obs if attempt == 0 and a.obs is not None else self._look()
                el = match(obs, step["target"])
                if el is None:
                    # The screen may still be loading: wait for the target to appear.
                    ok, why, obs = self.wait_until({"element": step["target"]}, timeout)
                    el = match(obs, step["target"]) if ok else None
                    if el is None:
                        raise StepFailed(f"target {step['target']} not found")
                args["id"] = el.id
            elif a.obs is None:
                self._look()
            out = a._dispatch(tool, dict(args))
            a.log.event("action", step=step_no, tool=tool, input=args, reason=label, result=str(out)[:2000])
            if "expect" not in step:
                time.sleep(a.settings.settle_seconds if tool in ("click", "key", "type_text") else 0)
                self._look()
                return str(out)
            time.sleep(min(a.settings.settle_seconds, 0.5))
            ok, last_why, _ = self.wait_until(step["expect"], timeout)
            if ok:
                return f"{out}; expect ok"
        raise StepFailed(f"expectation not met after {retries + 1} attempt(s): {last_why}")
