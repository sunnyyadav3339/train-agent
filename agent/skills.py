"""Skills: what the agent knows how to do lives in skills/<name>/, never in code.

    skills/<name>/
      SKILL.md     frontmatter (name, description, triggers, params) + instructions
      steps.yaml   optional fast path: actions the harness replays without the model
      lessons.md   notes written back after runs

The harness puts a catalog of all skills in the prompt, auto-loads the best match for the
task, and lets the model load others (`load_skill`) or replay a fast path (`run_skill_steps`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.S)
MIN_SCORE = 2


@dataclass
class Skill:
    name: str
    description: str
    triggers: list[str]
    params: list[str]
    body: str
    path: Path
    steps: list[dict] = field(default_factory=list)
    lessons: str = ""

    def full_text(self) -> str:
        parts = [f"# Skill: {self.name}\n{self.description}\n\n{self.body.strip()}"]
        if self.steps:
            params = ", ".join(self.params) or "none"
            parts.append(f"This skill has a fast path ({len(self.steps)} steps). Run it with "
                         f"run_skill_steps(name=\"{self.name}\", params={{...}}); params: {params}. "
                         f"If it stops, continue from the current screen yourself.")
        if self.lessons.strip():
            parts.append(f"## Lessons from earlier runs\n{self.lessons.strip()}")
        return "\n\n".join(parts)


def load_skill(folder: Path) -> Skill | None:
    md = folder / "SKILL.md"
    if not md.exists():
        return None
    text = md.read_text(encoding="utf-8")
    m = FRONTMATTER.match(text)
    meta, body = (yaml.safe_load(m.group(1)) or {}, m.group(2)) if m else ({}, text)
    steps_file = folder / "steps.yaml"
    steps_doc = yaml.safe_load(steps_file.read_text(encoding="utf-8")) if steps_file.exists() else None
    doc = steps_doc if isinstance(steps_doc, dict) else {}
    steps = doc.get("steps", []) if doc else (steps_doc or [])
    params = meta.get("params") or doc.get("params") or []
    lessons_file = folder / "lessons.md"
    return Skill(
        name=str(meta.get("name") or folder.name),
        description=str(meta.get("description") or "").strip(),
        triggers=[str(t).lower() for t in (meta.get("triggers") or [])],
        params=[str(p) for p in params],
        body=body,
        path=folder,
        steps=steps,
        lessons=lessons_file.read_text(encoding="utf-8") if lessons_file.exists() else "",
    )


def load_all(skills_dir: Path) -> dict[str, Skill]:
    skills: dict[str, Skill] = {}
    if not skills_dir.exists():
        return skills
    for folder in sorted(p for p in skills_dir.iterdir() if p.is_dir()):
        s = load_skill(folder)
        if s:
            skills[s.name] = s
    return skills


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2}


def score(skill: Skill, task: str) -> int:
    t = task.lower()
    s = sum(3 for trig in skill.triggers if trig and trig in t)
    s += len(_words(task) & _words(f"{skill.name.replace('-', ' ')} {skill.description}"))
    return s


def select(skills: dict[str, Skill], task: str) -> Skill | None:
    ranked = sorted(skills.values(), key=lambda sk: score(sk, task), reverse=True)
    return ranked[0] if ranked and score(ranked[0], task) >= MIN_SCORE else None


def catalog(skills: dict[str, Skill]) -> str:
    if not skills:
        return ""
    lines = ["Available skills (load one with load_skill when it fits the task):"]
    lines += [f"- {s.name}: {s.description}" + (" [fast path]" if s.steps else "") for s in skills.values()]
    return "\n".join(lines)


# --- templating ---------------------------------------------------------------
_TOKEN = re.compile(r"\{([^{}]+)\}")


def _date_value(expr: str) -> str:
    """today, today+1, today-2, optionally with a strftime format: {today+1:%d-%m-%Y}."""
    m = re.fullmatch(r"(today|now)([+-]\d+)?(?::(.+))?", expr)
    if not m:
        raise KeyError(expr)
    base = datetime.now() if m.group(1) == "now" else datetime.combine(date.today(), datetime.min.time())
    d = base + timedelta(days=int(m.group(2) or 0))
    return d.strftime(m.group(3) or ("%Y-%m-%d %H:%M" if m.group(1) == "now" else "%Y-%m-%d"))


def _lookup(path: str, ctx: dict):
    cur = ctx
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit():
            cur = cur[int(part)]
        else:
            raise KeyError(path)
    return cur


FILTERS = {
    "basename": lambda v: Path(str(v)).name,
    "stem": lambda v: Path(str(v)).stem,
    "upper": lambda v: str(v).upper(),
    "lower": lambda v: str(v).lower(),
    "strip": lambda v: str(v).strip(),
}


def render(value, ctx: dict):
    """Fill {param}, {data.file.key}, {today+1:%d-%m-%Y}, {path|basename} in strings (recursively).

    Write {{ and }} for literal braces (e.g. PowerShell script blocks).
    """
    if isinstance(value, dict):
        return {k: render(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [render(v, ctx) for v in value]
    if not isinstance(value, str):
        return value

    def sub(m: re.Match) -> str:
        expr, *filters = [p.strip() for p in m.group(1).split("|")]
        try:
            v = _lookup(expr, ctx)
        except KeyError:
            v = _date_value(expr)
        for f in filters:
            v = FILTERS[f](v)
        return str(v)

    escaped = value.replace("{{", "\x00").replace("}}", "\x01")
    return _TOKEN.sub(sub, escaped).replace("\x00", "{").replace("\x01", "}")
