"""Settings loaded from .env. Everything tunable lives here, not in code."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


@dataclass(frozen=True)
class RouteConfig:
    provider: str  # gemini | groq | openrouter | ollama
    model: str
    vision: bool   # can this model read screenshots?
    rpm: int       # self-imposed requests per minute (0 = no limit), keeps free tiers happy


DEFAULT_ROUTES = (
    "gemini/gemini-3-flash-preview, gemini/gemini-3.5-flash-lite, gemini/gemini-3.1-flash-lite, "
    "groq/qwen/qwen3.8-27b, groq/openai/gpt-oss-120b:text"
)


def parse_routes(spec: str) -> list[RouteConfig]:
    """'provider/model[:text][@rpm], ...' in priority order. ':text' = cannot read screenshots."""
    routes = []
    for item in (s.strip() for s in spec.split(",")):
        if not item:
            continue
        rpm = 0
        if "@" in item:
            item, rpm_s = item.rsplit("@", 1)
            rpm = int(rpm_s)
        vision = True
        if item.endswith(":text"):
            item, vision = item[: -len(":text")], False
        provider, _, model = item.partition("/")
        if not model:
            raise ValueError(f"Bad route '{item}': expected provider/model")
        routes.append(RouteConfig(provider.strip(), model.strip(), vision, rpm))
    if not routes:
        raise ValueError("MODEL_ROUTES is empty")
    return routes


@dataclass(frozen=True)
class Settings:
    # Models in priority order. Each call uses the first one that is available.
    routes: list[RouteConfig] = field(default_factory=lambda: parse_routes(_env("MODEL_ROUTES", DEFAULT_ROUTES)))
    max_steps: int = field(default_factory=lambda: int(_env("MAX_STEPS", "60")))
    # Screenshots sent to the model are scaled so the long edge is at most this.
    max_long_edge: int = field(default_factory=lambda: int(_env("MAX_LONG_EDGE", "1280")))
    # Max numbered elements listed per observation.
    max_elements: int = field(default_factory=lambda: int(_env("MAX_ELEMENTS", "120")))
    # Seconds to let the UI settle after actions before observing.
    settle_seconds: float = field(default_factory=lambda: float(_env("SETTLE_SECONDS", "0.6")))
    shell_timeout: int = field(default_factory=lambda: int(_env("SHELL_TIMEOUT", "120")))
    runs_dir: Path = field(default_factory=lambda: ROOT / _env("RUNS_DIR", "runs"))
    skills_dir: Path = ROOT / "skills"
    data_dir: Path = ROOT / "data"


settings = Settings()
