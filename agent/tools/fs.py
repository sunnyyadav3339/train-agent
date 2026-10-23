"""File read, write and list."""

from __future__ import annotations

from pathlib import Path

MAX_READ = 20000


def read_file(path: str) -> str:
    p = Path(path).expanduser()
    data = p.read_text(encoding="utf-8", errors="replace")
    if len(data) > MAX_READ:
        return data[:MAX_READ] + f"\n... [truncated, {len(data)} chars total]"
    return data


def write_file(path: str, content: str, append: bool = False) -> str:
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a" if append else "w", encoding="utf-8") as f:
        f.write(content)
    return f"Wrote {len(content)} chars to {p.resolve()}"


def list_dir(path: str) -> str:
    p = Path(path).expanduser()
    entries = sorted(p.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
    lines = [f"{'[dir] ' if e.is_dir() else ''}{e.name}" for e in entries[:500]]
    if len(entries) > 500:
        lines.append(f"... {len(entries) - 500} more")
    return "\n".join(lines) or "(empty)"
