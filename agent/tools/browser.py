"""Browser hands: a real, visible Chrome driven through Playwright.

The agent gets its own Chrome profile (logins persist between runs, separate from your
everyday profile). While its window is in front, clicks and keys go straight into the
page, in page (CSS) pixels, so they land exactly and cannot hit another window.
"""

from __future__ import annotations

import time
from pathlib import Path

from .desktop import focus_window, parse_combo

# pyautogui key names (from parse_combo) -> Playwright key names.
PW_KEYS = {
    "enter": "Enter", "esc": "Escape", "backspace": "Backspace", "delete": "Delete", "tab": "Tab",
    "space": " ", "pageup": "PageUp", "pagedown": "PageDown", "home": "Home", "end": "End",
    "insert": "Insert", "up": "ArrowUp", "down": "ArrowDown", "left": "ArrowLeft", "right": "ArrowRight",
    "ctrl": "Control", "ctrlright": "Control", "alt": "Alt", "altright": "Alt",
    "shift": "Shift", "shiftright": "Shift", "win": "Meta", "capslock": "CapsLock",
}

DOM_ELEMENTS_JS = r"""
(max) => {
  const sel = 'a[href], button, input, select, textarea, summary, [role=button], [role=link], ' +
    '[role=checkbox], [role=radio], [role=tab], [role=menuitem], [role=option], [role=combobox], ' +
    '[role=textbox], [role=switch], [role=searchbox], [onclick], [contenteditable=""], ' +
    '[contenteditable=true], [tabindex]:not([tabindex="-1"])';
  const W = innerWidth, H = innerHeight, out = [], seen = new Set();
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    if (r.width < 3 || r.height < 3 || r.bottom <= 0 || r.right <= 0 || r.top >= H || r.left >= W) continue;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none' || +cs.opacity === 0) continue;
    if (el.disabled) continue;
    const cx = Math.min(Math.max(r.left + r.width / 2, 0), W - 1);
    const cy = Math.min(Math.max(r.top + r.height / 2, 0), H - 1);
    const top = document.elementFromPoint(cx, cy);
    if (top && top !== el && !el.contains(top) && !top.contains(el)) continue;  // covered by something
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    const role = el.getAttribute('role') || (tag === 'input' ? 'input:' + (type || 'text') : tag);
    let name = el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.getAttribute('title') ||
               el.getAttribute('alt') || '';
    if (!name && el.labels && el.labels[0]) name = el.labels[0].innerText;
    if (!name) name = el.innerText || el.textContent || el.getAttribute('name') || el.id || '';
    name = String(name).replace(/\s+/g, ' ').trim().slice(0, 80);
    let value = '';
    if (tag === 'input' || tag === 'textarea') {
      if (type === 'password') value = el.value ? '(filled)' : '';
      else if (type === 'checkbox' || type === 'radio') value = el.checked ? 'checked' : 'unchecked';
      else value = el.value || '';
    } else if (tag === 'select') {
      value = el.options[el.selectedIndex] ? el.options[el.selectedIndex].text : '';
    }
    const key = [r.left, r.top, r.width, r.height].map(Math.round).join(',');
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({ role, name, value: String(value).replace(/\s+/g, ' ').slice(0, 60),
               rect: [Math.max(r.left, 0), Math.max(r.top, 0), Math.min(r.right, W), Math.min(r.bottom, H)] });
    if (out.length >= max) break;
  }
  return out;
}
"""


def pw_combo(combo: str) -> str:
    return "+".join(PW_KEYS.get(k, k) for k in parse_combo(combo))


class Browser:
    """Lazily launched Chrome with one persistent agent profile."""

    def __init__(self, profile_dir: Path, downloads_dir: Path, channel: str = "chrome"):
        self.profile_dir = Path(profile_dir)
        self.downloads_dir = Path(downloads_dir)
        self.channel = channel
        self._pw = None
        self.ctx = None
        self.page = None
        self.hwnds: set[int] = set()  # OS windows that belong to the agent's Chrome
        self.notes: list[str] = []  # dialogs, downloads, new tabs: reported in the next observation

    # --- lifecycle --------------------------------------------------------
    @property
    def running(self) -> bool:
        return self.ctx is not None and self.page is not None and not self.page.is_closed()

    def ensure(self) -> None:
        if self.running:
            return
        if self.ctx is None:
            from playwright.sync_api import sync_playwright
            self.profile_dir.mkdir(parents=True, exist_ok=True)
            self.downloads_dir.mkdir(parents=True, exist_ok=True)
            self._pw = sync_playwright().start()
            kwargs = dict(headless=False, no_viewport=True, accept_downloads=True,
                          args=["--start-maximized"])
            try:
                self.ctx = self._pw.chromium.launch_persistent_context(
                    str(self.profile_dir), channel=self.channel, **kwargs)
            except Exception:
                # Chrome not installed: fall back to Playwright's bundled Chromium.
                self.ctx = self._pw.chromium.launch_persistent_context(str(self.profile_dir), **kwargs)
            self.ctx.on("page", self._on_new_page)
            for p in self.ctx.pages:
                self._wire(p)
        pages = [p for p in self.ctx.pages if not p.is_closed()]
        self.page = pages[-1] if pages else self.ctx.new_page()
        self._wire(self.page)

    def close(self) -> None:
        try:
            if self.ctx:
                self.ctx.close()
            if self._pw:
                self._pw.stop()
        finally:
            self.ctx = self.page = self._pw = None
            self.hwnds.clear()

    def _wire(self, page) -> None:
        if getattr(page, "_agent_wired", False):
            return
        page._agent_wired = True
        page.on("dialog", self._on_dialog)
        page.on("download", self._on_download)

    def _on_new_page(self, page) -> None:
        self._wire(page)
        self.page = page
        self.notes.append("A new tab opened and is now the active tab.")

    def _on_dialog(self, dialog) -> None:
        # The agent's own click raised it, so confirm; prompts have no answer, so dismiss.
        kind, msg = dialog.type, dialog.message
        if kind == "prompt":
            dialog.dismiss()
            self.notes.append(f'Page prompt dismissed: "{msg}"')
        else:
            dialog.accept()
            self.notes.append(f'Page {kind} accepted: "{msg}"')

    def _on_download(self, download) -> None:
        target = self.downloads_dir / download.suggested_filename
        download.save_as(str(target))
        self.notes.append(f"Downloaded file saved to {target}")

    def take_notes(self) -> list[str]:
        notes, self.notes = self.notes, []
        return notes

    # --- state ------------------------------------------------------------
    def window_title(self) -> str:
        return f"{self.page.title()} - Google Chrome" if self.running else ""

    def is_foreground(self, foreground_title: str) -> bool:
        """Is the agent's Chrome the window in front? Matched by window handle, so a page
        whose title is still changing (mid-navigation) is recognised too."""
        if not self.running:
            return False
        from ..eyes.marks import foreground_hwnd
        if foreground_hwnd() in self.hwnds:
            return True
        try:
            title = self.page.title()
        except Exception:
            return False
        if foreground_title.endswith("Google Chrome") and title and foreground_title.startswith(title):
            self.hwnds.add(foreground_hwnd())
            return True
        return False

    def tabs(self) -> list[str]:
        out = []
        for i, p in enumerate(self.ctx.pages if self.ctx else []):
            if p.is_closed():
                continue
            mark = " (active)" if p == self.page else ""
            out.append(f"[{i}] {p.title()[:60]} - {p.url[:100]}{mark}")
        return out

    def viewport(self) -> dict:
        return self.page.evaluate("() => ({w: innerWidth, h: innerHeight, dpr: devicePixelRatio})")

    def screenshot_png(self, clip: dict | None = None) -> bytes:
        return self.page.screenshot(clip=clip, animations="disabled", caret="initial", timeout=15000)

    def elements(self, max_elements: int) -> list[dict]:
        return self.page.evaluate(DOM_ELEMENTS_JS, max_elements)

    def settle(self, timeout_ms: int = 5000) -> None:
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
        except Exception:
            pass
        time.sleep(0.3)

    # --- navigation -------------------------------------------------------
    def open(self, url: str, new_tab: bool = False) -> str:
        self.ensure()
        if "://" not in url and not url.startswith("about:"):
            url = "https://" + url
        if new_tab:
            self.page = self.ctx.new_page()
            self._wire(self.page)
        self.page.goto(url, wait_until="domcontentloaded", timeout=45000)
        self.front()
        return f'Opened {self.page.url} ("{self.page.title()}")'

    def front(self) -> None:
        """Bring the agent's Chrome window and the active tab to the front."""
        from ..eyes.marks import foreground_hwnd, foreground_title
        self.page.bring_to_front()
        title = self.page.title()
        # An untitled page shows its URL in the window title instead.
        label = title or self.page.url.split("://", 1)[-1].rstrip("/")
        try:
            focus_window(f"{label} - Google Chrome")
        except Exception:
            pass
        if foreground_title().endswith("Google Chrome"):
            self.hwnds.add(foreground_hwnd())

    def back(self) -> str:
        self.page.go_back(wait_until="domcontentloaded", timeout=30000)
        return f"Back to {self.page.url}"

    def switch_tab(self, index: int) -> str:
        pages = self.ctx.pages
        if not 0 <= index < len(pages) or pages[index].is_closed():
            raise IndexError(f"No tab {index}. Tabs: {self.tabs()}")
        self.page = pages[index]
        self.front()
        return f"Switched to tab {index}: {self.page.title()}"

    def close_tab(self) -> str:
        url = self.page.url
        self.page.close()
        pages = [p for p in self.ctx.pages if not p.is_closed()]
        self.page = pages[-1] if pages else self.ctx.new_page()
        self.front()
        return f"Closed {url}"

    def read_text(self, max_chars: int = 8000) -> str:
        text = self.page.evaluate("() => document.body ? document.body.innerText : ''")
        text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
        return text if len(text) <= max_chars else text[:max_chars] + f"\n... [{len(text)} chars total]"

    # --- input (CSS pixels in the viewport) -------------------------------
    def click(self, x: float, y: float, button: str = "left", clicks: int = 1, modifiers: str | None = None) -> str:
        mods = [PW_KEYS.get(k, k) for k in parse_combo(modifiers)] if modifiers else []
        for m in mods:
            self.page.keyboard.down(m)
        try:
            self.page.mouse.click(x, y, button=button, click_count=clicks)
        finally:
            for m in reversed(mods):
                self.page.keyboard.up(m)
        return f"Clicked {button} x{clicks} at page ({round(x)}, {round(y)})"

    def type_text(self, text: str) -> str:
        self.page.keyboard.type(text, delay=15)
        return f"Typed {len(text)} chars"

    def key(self, combo: str, repeat: int = 1) -> str:
        k = pw_combo(combo)
        for _ in range(max(1, int(repeat))):
            self.page.keyboard.press(k)
        return f"Pressed {combo}" + (f" x{repeat}" if repeat > 1 else "")

    def scroll(self, direction: str, amount: int, x: float | None = None, y: float | None = None) -> str:
        if x is None or y is None:
            vp = self.viewport()
            x, y = vp["w"] / 2, vp["h"] / 2
        self.page.mouse.move(x, y)
        step = 120 * int(amount)
        dx = step if direction == "right" else -step if direction == "left" else 0
        dy = step if direction == "down" else -step if direction == "up" else 0
        self.page.mouse.wheel(dx, dy)
        time.sleep(0.3)
        return f"Scrolled {direction} {amount}"

    def drag(self, x0: float, y0: float, x1: float, y1: float) -> str:
        self.page.mouse.move(x0, y0)
        self.page.mouse.down()
        self.page.mouse.move(x1, y1, steps=12)
        self.page.mouse.up()
        return "Dragged"
