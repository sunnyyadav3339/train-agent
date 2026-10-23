"""Terminal: run a command and capture its output (the terminal-output half of the eyes)."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile

MAX_OUTPUT = 8000
# Models often wrap commands in another `powershell -Command "..."`, which breaks $variables.
_NESTED = re.compile(r'^\s*powershell(?:\.exe)?\s+(?:-\w+\s+)*-Command\s+(["\'])(.*)\1\s*$', re.I | re.S)


def unwrap(command: str) -> str:
    m = _NESTED.match(command)
    return m.group(2) if m else command


def run_shell(command: str, timeout: int, cwd: str | None = None) -> str:
    script = None
    if sys.platform == "win32":
        # Run from a script file: passes the command verbatim (no quoting or $-expansion
        # surprises) and keeps errors as plain text.
        fd, script = tempfile.mkstemp(suffix=".ps1", prefix="agent_")
        with os.fdopen(fd, "w", encoding="utf-8-sig") as f:
            f.write("$ProgressPreference = 'SilentlyContinue'\n"
                    "[Console]::OutputEncoding = [Text.Encoding]::UTF8\n" + unwrap(command) + "\n")
        argv = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", script]
    else:
        argv = ["bash", "-lc", command]
    try:
        proc = subprocess.run(
            argv, cwd=cwd, capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        return _clip(f"TIMEOUT after {timeout}s\n{out}")
    finally:
        if script:
            os.unlink(script)
    parts = [f"exit_code: {proc.returncode}"]
    if proc.stdout:
        parts.append(f"stdout:\n{proc.stdout}")
    if proc.stderr:
        parts.append(f"stderr:\n{proc.stderr}")
    return _clip("\n".join(parts))


def _clip(text: str) -> str:
    if len(text) <= MAX_OUTPUT:
        return text
    half = MAX_OUTPUT // 2
    return f"{text[:half]}\n... [{len(text) - MAX_OUTPUT} chars truncated] ...\n{text[-half:]}"
