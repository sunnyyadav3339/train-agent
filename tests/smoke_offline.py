"""Offline checks for hands and eyes (no API key needed): python -m tests.smoke_offline"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from agent.config import settings
from agent.eyes.marks import observe, open_windows
from agent.eyes.screen import API_MAX_VISUAL_TOKENS, Screen, fit_scale
from agent.models import _strip_images
from agent.tools import TOOLS
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

    print(f"\n{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
