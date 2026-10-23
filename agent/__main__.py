"""Chat-style entry point: python -m agent "open notepad and write hello"."""

from __future__ import annotations

import argparse
import dataclasses
import sys

from .config import parse_routes, settings
from .loop import Agent


def main() -> int:
    parser = argparse.ArgumentParser(prog="agent", description="Run the computer-use agent on a task.")
    parser.add_argument("task", nargs="+", help="What the agent should do.")
    parser.add_argument("--routes", help="Override MODEL_ROUTES, e.g. 'gemini/gemini-3-flash-preview'.")
    parser.add_argument("--max-steps", type=int, help="Override MAX_STEPS.")
    args = parser.parse_args()

    cfg = settings
    if args.routes:
        cfg = dataclasses.replace(cfg, routes=parse_routes(args.routes))
    if args.max_steps:
        cfg = dataclasses.replace(cfg, max_steps=args.max_steps)

    result = Agent(cfg).run(" ".join(args.task))
    return 0 if result.status == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
