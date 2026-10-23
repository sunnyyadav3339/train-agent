"""Screen capture and the coordinate space the model sees.

The model works in the pixel space of the (possibly downscaled) screenshots we send it.
`Screen` owns the scale factor so every click can be mapped back to real pixels.
"""

from __future__ import annotations

import ctypes
import io
import math
import sys

import mss
from PIL import Image

# Limits for images in tool results (computer_toolset_20260801 models).
API_MAX_LONG_EDGE = 2576
API_MAX_VISUAL_TOKENS = 4784  # ceil(w/28) * ceil(h/28)


def make_dpi_aware() -> None:
    """Use physical pixels everywhere so mss and pyautogui agree on coordinates."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor aware
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def fit_scale(width: int, height: int, max_long_edge: int) -> float:
    """Largest scale <= 1 that keeps a screenshot inside our and the API's limits."""
    scale = min(1.0, max_long_edge / max(width, height), API_MAX_LONG_EDGE / max(width, height))
    while math.ceil(width * scale / 28) * math.ceil(height * scale / 28) > API_MAX_VISUAL_TOKENS:
        scale *= 0.97
    return scale


class Screen:
    """Primary monitor capture with model <-> screen coordinate mapping."""

    def __init__(self, max_long_edge: int):
        make_dpi_aware()
        self._sct = mss.mss()
        mon = self._sct.monitors[1]
        self.left, self.top = mon["left"], mon["top"]
        self.width, self.height = mon["width"], mon["height"]
        self.scale = fit_scale(self.width, self.height, max_long_edge)
        self.model_width = round(self.width * self.scale)
        self.model_height = round(self.height * self.scale)

    def capture(self) -> Image.Image:
        """Screenshot already resized to model space."""
        raw = self._sct.grab(self._sct.monitors[1])
        img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
        if self.scale < 1.0:
            img = img.resize((self.model_width, self.model_height), Image.LANCZOS)
        return img

    def crop(self, region: list[float]) -> Image.Image:
        """Full-resolution crop of a model-space region, fitted to the same limits (for zoom)."""
        x0, y0 = self.to_screen(region[0], region[1])
        x1, y1 = self.to_screen(region[2], region[3])
        raw = self._sct.grab({"left": x0, "top": y0, "width": max(1, x1 - x0), "height": max(1, y1 - y0)})
        img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
        s = fit_scale(img.width, img.height, max(self.model_width, self.model_height))
        if s < 1.0:
            img = img.resize((round(img.width * s), round(img.height * s)), Image.LANCZOS)
        return img

    def to_screen(self, x: float, y: float) -> tuple[int, int]:
        """Model-space point -> real screen pixels, clamped to the monitor."""
        sx = min(max(round(x / self.scale), 0), self.width - 1) + self.left
        sy = min(max(round(y / self.scale), 0), self.height - 1) + self.top
        return sx, sy

    def to_model(self, sx: float, sy: float) -> tuple[int, int]:
        """Real screen pixels -> model space (used for UI tree rectangles)."""
        return round((sx - self.left) * self.scale), round((sy - self.top) * self.scale)


def png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
