"""Set-of-Marks: number every useful element on screen so the model can say `click(17)`.

Elements come from structure: the Windows UI Automation tree for desktop apps, the page DOM
when the agent's browser is in front. The same numbers are drawn on the screenshot, so a
vision model can match what it sees to an id, and a text-only model can still act from the
list alone.

An observation has a mode. "desktop": surface pixels are real screen pixels. "browser":
surface pixels are CSS pixels of the page viewport. `to_model` / `to_surface` convert
between surface pixels and the screenshot space the model sees.
"""

from __future__ import annotations

import ctypes
import io
import time
from dataclasses import dataclass, field
from typing import Callable

from PIL import Image, ImageDraw, ImageFont

from .screen import Screen, fit_scale

CLICKABLE = {
    "Button", "SplitButton", "Edit", "ComboBox", "CheckBox", "RadioButton", "Hyperlink",
    "ListItem", "MenuItem", "TabItem", "TreeItem", "DataItem", "Slider", "Spinner", "Document",
    "Thumb", "HeaderItem",
}
READABLE = {"Text", "Image", "StatusBar", "TitleBar"}
COLORS = ["#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4", "#008080", "#9a6324", "#800000"]
TIME_BUDGET_S = 2.5


@dataclass
class Element:
    id: int
    role: str
    name: str
    value: str
    rect: tuple[float, float, float, float]  # surface pixels: left, top, right, bottom
    source: str = "uia"

    @property
    def center(self) -> tuple[float, float]:
        l, t, r, b = self.rect
        return (l + r) / 2, (t + b) / 2

    def line(self) -> str:
        v = f' value="{self.value[:60]}"' if self.value else ""
        return f'[{self.id}] {self.role} "{self.name[:70]}"{v}'


@dataclass
class Observation:
    image: Image.Image          # annotated screenshot in model space
    raw: Image.Image            # clean screenshot in model space (for the run log)
    elements: dict[int, Element]
    window: str
    windows: list[str]
    mode: str = "desktop"       # desktop | browser
    to_model: Callable[[float, float], tuple[float, float]] = field(default=lambda x, y: (x, y), repr=False)
    to_surface: Callable[[float, float], tuple[float, float]] = field(default=lambda x, y: (x, y), repr=False)
    url: str = ""
    tabs: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def text(self) -> str:
        lines = [f'Active window: "{self.window}"']
        if self.mode == "browser":
            lines.append(f"Browser (the agent's Chrome) is in front. URL: {self.url}")
            if len(self.tabs) > 1:
                lines.append("Tabs: " + " | ".join(self.tabs))
            lines.append("The screenshot shows the page area only. Use read_page for the full page text.")
        if self.windows:
            lines.append("Open windows: " + " | ".join(self.windows[:15]))
        for n in self.notes:
            lines.append(f"Note: {n}")
        if self.elements:
            lines.append("Elements (use the id with click / type_text / scroll):")
            lines.extend(e.line() for e in self.elements.values())
        else:
            lines.append("No structured elements found; use click_xy on the screenshot.")
        return "\n".join(lines)


def _foreground():
    from pywinauto import Desktop
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    return Desktop(backend="uia").window(handle=hwnd).wrapper_object() if hwnd else None


def _title(hwnd) -> str:
    user32 = ctypes.windll.user32
    n = user32.GetWindowTextLengthW(hwnd)
    if not n:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def top_windows() -> list[tuple[int, str]]:
    """(handle, title) of visible top-level windows, front to back (plain Win32: fast)."""
    user32 = ctypes.windll.user32
    found: list[tuple[int, str]] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def visit(hwnd, _):
        if user32.IsWindowVisible(hwnd) and not user32.GetWindow(hwnd, 4):  # skip owned popups
            title = _title(hwnd)
            if title.strip() and title not in ("Program Manager", "Taskbar"):
                found.append((hwnd, title))
        return True

    user32.EnumWindows(visit, 0)
    return found


def open_windows() -> list[str]:
    return [t for _, t in top_windows()]


def foreground_hwnd() -> int:
    return ctypes.windll.user32.GetForegroundWindow() or 0


def foreground_title() -> str:
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    return _title(hwnd) if hwnd else ""


def collect_uia(screen: Screen, max_elements: int) -> tuple[str, list[Element]]:
    """Breadth-first walk of the foreground window, nearest-to-top elements first."""
    try:
        win = _foreground()
    except Exception:
        return "", []
    if win is None:
        return "", []
    title = win.window_text()
    try:
        wr = win.element_info.rectangle
        bounds = (max(wr.left, screen.left), max(wr.top, screen.top),
                  min(wr.right, screen.left + screen.width), min(wr.bottom, screen.top + screen.height))
    except Exception:
        bounds = (screen.left, screen.top, screen.left + screen.width, screen.top + screen.height)

    found: list[Element] = []
    seen: set[tuple] = set()
    queue = [win.element_info]
    deadline = time.time() + TIME_BUDGET_S
    while queue and len(found) < max_elements and time.time() < deadline:
        info = queue.pop(0)
        try:
            children = info.children()
        except Exception:
            continue
        for ch in children:
            queue.append(ch)
            try:
                role = ch.control_type or ""
                name = (ch.name or "").strip()
                r = ch.rectangle
            except Exception:
                continue
            if role not in CLICKABLE and not (role in READABLE and name):
                continue
            l, t, rr, b = max(r.left, bounds[0]), max(r.top, bounds[1]), min(r.right, bounds[2]), min(r.bottom, bounds[3])
            if rr - l < 3 or b - t < 3:
                continue  # off-screen or clipped away
            key = (role, name, l, t, rr, b)
            if key in seen:
                continue
            seen.add(key)
            value = ""
            if role in ("Edit", "ComboBox", "Document"):
                try:
                    value = (ch.element.CurrentPropertyValue(30045) or "")  # UIA ValueValue
                except Exception:
                    value = ""
            found.append(Element(0, role, name, str(value).replace("\n", " "), (l, t, rr, b)))
            if len(found) >= max_elements:
                break
    return title, found


def annotate(img: Image.Image, elements: list[Element], to_model) -> Image.Image:
    out = img.copy()
    draw = ImageDraw.Draw(out)
    try:
        font = ImageFont.truetype("arial.ttf", 13)
    except OSError:
        font = ImageFont.load_default()
    for e in elements:
        color = COLORS[e.id % len(COLORS)]
        l, t = to_model(e.rect[0], e.rect[1])
        r, b = to_model(e.rect[2], e.rect[3])
        draw.rectangle([l, t, r, b], outline=color, width=2)
        label = str(e.id)
        tw, th = draw.textbbox((0, 0), label, font=font)[2:]
        lx, ly = l, max(t - th - 2, 0)
        draw.rectangle([lx, ly, lx + tw + 4, ly + th + 2], fill=color)
        draw.text((lx + 2, ly + 1), label, fill="white", font=font)
    return out


def _number(elements: list[Element]) -> None:
    """Number top-to-bottom, left-to-right so ids read naturally."""
    elements.sort(key=lambda e: (e.rect[1] // 10, e.rect[0]))
    for i, e in enumerate(elements, start=1):
        e.id = i


def observe(screen: Screen, max_elements: int, browser=None) -> Observation:
    fg = foreground_title()
    if browser is not None and browser.is_foreground(fg):
        try:
            return observe_browser(screen, max_elements, browser, fg)
        except Exception:
            pass  # page busy or mid-navigation: fall back to the desktop view
    raw = screen.capture()
    title, elements = collect_uia(screen, max_elements)
    _number(elements)
    notes = browser.take_notes() if browser is not None else []
    return Observation(
        image=annotate(raw, elements, screen.to_model),
        raw=raw,
        elements={e.id: e for e in elements},
        window=fg or title,
        windows=open_windows(),
        to_model=screen.to_model,
        to_surface=lambda x, y: screen.to_screen(x, y),
        notes=notes,
    )


def observe_browser(screen: Screen, max_elements: int, browser, fg: str) -> Observation:
    """Page-area screenshot plus numbered DOM elements, all in CSS pixels of the viewport."""
    browser.settle(timeout_ms=3000)
    vp = browser.viewport()
    shot = Image.open(io.BytesIO(browser.screenshot_png())).convert("RGB")
    scale = fit_scale(shot.width, shot.height, max(screen.model_width, screen.model_height))
    if scale < 1.0:
        shot = shot.resize((round(shot.width * scale), round(shot.height * scale)), Image.LANCZOS)
    k = shot.width / vp["w"]  # model pixels per CSS pixel

    def to_model(x: float, y: float) -> tuple[float, float]:
        return x * k, y * k

    def to_surface(x: float, y: float) -> tuple[float, float]:
        return min(max(x / k, 0), vp["w"] - 1), min(max(y / k, 0), vp["h"] - 1)

    elements = [Element(0, d["role"], d["name"], d["value"], tuple(d["rect"]), "dom")
                for d in browser.elements(max_elements)]
    _number(elements)
    return Observation(
        image=annotate(shot, elements, to_model),
        raw=shot,
        elements={e.id: e for e in elements},
        window=fg,
        windows=open_windows(),
        mode="browser",
        to_model=to_model,
        to_surface=to_surface,
        url=browser.page.url,
        tabs=browser.tabs(),
        notes=browser.take_notes(),
    )
