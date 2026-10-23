"""Model router: any provider with an OpenAI-compatible chat API (Gemini, Groq, OpenRouter, Ollama).

Requests go straight to the provider named in .env. Routes are tried in priority order on
every call, so the best available model always answers:
- daily quota used up  -> that route is skipped for the rest of the run
- rate limited, overloaded, request too large, or a bad request -> try the next route now
- every route busy     -> wait (honouring retry-after) and go round again
A 429 at 11:00 therefore costs one hop to the next model, not the run.
"""

from __future__ import annotations

import json
import os
import random
import re
import time
from dataclasses import dataclass, field

import openai

from .config import RouteConfig, Settings

PROVIDERS = {
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai/", "GEMINI_API_KEY"),
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "ollama": ("http://localhost:11434/v1", ""),
}
LONG_WAIT_S = 120  # a retry-after longer than this means "come back tomorrow"


@dataclass
class Route:
    label: str
    cfg: RouteConfig
    client: openai.OpenAI
    last_call: float = 0.0
    exhausted: str = ""            # reason, once the route is out for this run
    busy_until: float = field(default=0.0)

    def pace(self) -> None:
        if self.cfg.rpm > 0:
            gap = 60.0 / self.cfg.rpm - (time.time() - self.last_call)
            if gap > 0:
                time.sleep(gap)
        self.last_call = time.time()


def _client(cfg: RouteConfig) -> openai.OpenAI:
    if cfg.provider not in PROVIDERS:
        raise ValueError(f"Unknown provider '{cfg.provider}'. Use one of: {', '.join(PROVIDERS)}")
    base_url, key_env = PROVIDERS[cfg.provider]
    key = os.getenv(key_env, "").strip() if key_env else "ollama"
    if not key:
        raise ValueError(f"{key_env} is empty. Paste your key into .env.")
    return openai.OpenAI(api_key=key, base_url=base_url, max_retries=0, timeout=90)


def _wire(messages: list) -> list:
    """Drop harness bookkeeping keys (named __like_this__) before sending."""
    return [{k: v for k, v in m.items() if not k.startswith("__")} for m in messages]


def _strip_images(messages: list) -> list:
    """For text-only models: drop screenshots, keep everything else."""
    out = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):
            parts = [p for p in content if p.get("type") != "image_url"]
            if len(parts) != len(content):
                parts.append({"type": "text", "text": "[screenshot omitted: this model is text-only; "
                                                      "use the numbered element list]"})
            m = {**m, "content": parts}
        out.append(m)
    return out


def _wait_hint(exc: openai.APIStatusError) -> float | None:
    """Seconds the provider asks us to wait: retry-after header, or Google's retryDelay."""
    value = exc.response.headers.get("retry-after")
    try:
        if value:
            return float(value)
    except ValueError:
        pass
    m = re.search(r'"retryDelay":\s*"(\d+(?:\.\d+)?)s"', exc.response.text or "")
    return float(m.group(1)) if m else None


def _is_daily_quota(exc: openai.APIStatusError) -> bool:
    text = exc.response.text or ""
    return "PerDay" in text or "per day" in text.lower() or "(RPD)" in text or "(TPD)" in text


class ModelRouter:
    def __init__(self, settings: Settings, on_event=None):
        self.on_event = on_event or (lambda *a, **k: None)
        self.routes: list[Route] = []
        for i, cfg in enumerate(settings.routes):
            label = f"{cfg.provider}/{cfg.model}"
            try:
                self.routes.append(Route(label, cfg, _client(cfg)))
            except ValueError as exc:
                if i == 0:
                    raise
                self.on_event("route_disabled", route=label, reason=str(exc))

    def describe(self) -> list[str]:
        return [f"{r.label}{'' if r.cfg.vision else ' (text-only)'}" for r in self.routes]

    def chat(self, messages: list, tools: list, max_rounds: int = 4):
        """Returns (assistant message, route label). Raises when no route can answer."""
        errors: list[str] = []
        for round_no in range(max_rounds):
            for route in self.routes:
                if route.exhausted or route.busy_until > time.time():
                    continue
                route.pace()
                msgs = _wire(messages) if route.cfg.vision else _strip_images(_wire(messages))
                try:
                    resp = route.client.chat.completions.create(
                        model=route.cfg.model, messages=msgs, tools=tools,
                        tool_choice="auto", temperature=0.2, max_tokens=4096,
                    )
                    if not resp.choices:
                        raise ValueError("empty response")
                    return resp.choices[0].message, route.label
                except openai.APIStatusError as exc:
                    wait = _wait_hint(exc)
                    if exc.status_code == 429 and (_is_daily_quota(exc) or (wait or 0) > LONG_WAIT_S):
                        route.exhausted = "daily quota used up"
                    elif exc.status_code in (401, 403, 404):
                        route.exhausted = f"HTTP {exc.status_code}"
                    elif exc.status_code in (429, 503, 529):
                        route.busy_until = time.time() + min(wait or 10, LONG_WAIT_S)
                    # 400 / 413 / 5xx: this request does not suit this route; try the next one.
                    errors.append(f"{route.label}: {exc.status_code}")
                    self.on_event("route_failed", route=route.label, status=exc.status_code,
                                  exhausted=route.exhausted or None, detail=_short(exc))
                except (openai.APIConnectionError, ValueError, json.JSONDecodeError) as exc:
                    errors.append(f"{route.label}: {type(exc).__name__}")
                    self.on_event("route_failed", route=route.label, detail=str(exc)[:200])
            live = [r for r in self.routes if not r.exhausted]
            if not live:
                break
            # Everyone is busy: sleep until the soonest route frees up, then go round again.
            delay = min(r.busy_until for r in live) - time.time()
            if delay <= 0:
                delay = 2 ** round_no  # failures were not rate limits: brief backoff
            delay = min(delay + random.random(), LONG_WAIT_S)
            self.on_event("all_routes_busy", round=round_no + 1, delay_s=round(delay, 1))
            time.sleep(delay)
        raise RuntimeError("No model could answer: " + "; ".join(errors[-8:]))


def _short(exc: openai.APIStatusError) -> str:
    text = exc.response.text or str(exc)
    m = re.search(r'"message":\s*"([^"]{0,200})', text)
    return m.group(1) if m else text[:200]
