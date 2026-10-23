"""Mouse and keyboard on any desktop app. All coordinates here are real screen pixels."""

from __future__ import annotations

import ctypes
import time

import pyautogui
import pyperclip

pyautogui.FAILSAFE = True  # slam the mouse into a screen corner to abort
pyautogui.PAUSE = 0.05

# Friendly / xdotool-style key names -> pyautogui names.
KEY_ALIASES = {
    "return": "enter", "enter": "enter", "kp_enter": "enter",
    "escape": "esc", "esc": "esc",
    "backspace": "backspace", "back_space": "backspace",
    "delete": "delete", "del": "delete",
    "tab": "tab", "space": "space",
    "page_up": "pageup", "prior": "pageup", "pageup": "pageup", "pgup": "pageup",
    "page_down": "pagedown", "next": "pagedown", "pagedown": "pagedown", "pgdn": "pagedown",
    "home": "home", "end": "end", "insert": "insert",
    "up": "up", "down": "down", "left": "left", "right": "right",
    "arrowup": "up", "arrowdown": "down", "arrowleft": "left", "arrowright": "right",
    "ctrl": "ctrl", "control": "ctrl", "control_l": "ctrl", "control_r": "ctrlright",
    "alt": "alt", "alt_l": "alt", "alt_r": "altright",
    "shift": "shift", "shift_l": "shift", "shift_r": "shiftright",
    "super": "win", "super_l": "win", "win": "win", "windows": "win", "meta": "win", "cmd": "win",
    "caps_lock": "capslock", "print": "printscreen",
    "minus": "-", "plus": "+", "equal": "=", "comma": ",", "period": ".", "slash": "/",
}


def map_key(name: str) -> str:
    k = name.strip()
    low = k.lower()
    if low in KEY_ALIASES:
        return KEY_ALIASES[low]
    if low.startswith("f") and low[1:].isdigit():
        return low
    return low if len(k) > 1 else k


def parse_combo(combo: str) -> list[str]:
    parts = [p for p in combo.replace(" ", "").split("+") if p]
    return [map_key(p) for p in parts] if parts else ["+"]


class _Held:
    """Hold modifier keys for the duration of a mouse action."""

    def __init__(self, modifiers: str | None):
        self.keys = parse_combo(modifiers) if modifiers else []

    def __enter__(self):
        for k in self.keys:
            pyautogui.keyDown(k)

    def __exit__(self, *exc):
        for k in reversed(self.keys):
            pyautogui.keyUp(k)


def click(x: int, y: int, button: str = "left", clicks: int = 1, modifiers: str | None = None) -> str:
    with _Held(modifiers):
        pyautogui.click(x=x, y=y, button=button, clicks=clicks, interval=0.08)
    return f"Clicked {button} x{clicks} at ({x}, {y})"


def drag(x0: int, y0: int, x1: int, y1: int) -> str:
    pyautogui.moveTo(x0, y0)
    pyautogui.mouseDown()
    pyautogui.moveTo(x1, y1, duration=0.3)
    pyautogui.mouseUp()
    return "Dragged"


def scroll(direction: str, amount: int, x: int | None = None, y: int | None = None) -> str:
    if x is not None and y is not None:
        pyautogui.moveTo(x, y)
    clicks = 120 * int(amount)
    if direction in ("up", "down"):
        pyautogui.scroll(clicks if direction == "up" else -clicks)
    else:
        pyautogui.hscroll(clicks if direction == "right" else -clicks)
    return f"Scrolled {direction} {amount}"


def type_text(text: str) -> str:
    if text.isascii() and "\n" not in text:
        pyautogui.write(text, interval=0.01)
    else:
        # Non-ASCII or multi-line: paste through the clipboard, then restore it.
        previous = pyperclip.paste()
        pyperclip.copy(text)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.15)
        pyperclip.copy(previous)
    return f"Typed {len(text)} chars"


def key(combo: str, repeat: int = 1) -> str:
    keys = parse_combo(combo)
    for _ in range(max(1, int(repeat))):
        pyautogui.hotkey(*keys)
    return f"Pressed {combo}" + (f" x{repeat}" if repeat > 1 else "")


def focus_window(title_part: str) -> str:
    """Bring the front-most window whose title contains `title_part` to the front."""
    from ..eyes.marks import foreground_title, open_windows, top_windows
    user32 = ctypes.windll.user32
    needle = title_part.lower().strip()
    for hwnd, title in top_windows():
        if needle and needle in title.lower():
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            # Windows blocks focus stealing unless this process just sent input.
            pyautogui.press("alt")
            user32.SetForegroundWindow(hwnd)
            time.sleep(0.3)
            now = foreground_title()
            if now != title:
                raise RuntimeError(f'Tried to focus "{title}" but "{now}" is in front.')
            return f'Focused "{title}"'
    raise LookupError(f'No window title contains "{title_part}". Open windows: {open_windows()[:10]}')
