"""Hands: the action tools the model can call (OpenAI-style function definitions).

Every tool takes a required `reason`, which the run log stores next to the screenshot so
judges can replay why each step happened. Element ids refer to the latest observation.
"""

from __future__ import annotations

REASON = {"type": "string", "description": "One short sentence: why this action, what you expect to see after."}
BUTTON = {"type": "string", "enum": ["left", "right", "middle"]}
MODIFIERS = {"type": "string", "description": "Keys to hold during the click, e.g. 'ctrl' or 'ctrl+shift'."}


def _tool(name: str, description: str, props: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": {"reason": REASON, **props},
            "required": ["reason", *required],
        },
    }}


TOOLS = [
    _tool("click", "Click a numbered element from the latest observation. Preferred way to click.",
          {"id": {"type": "integer"}, "button": BUTTON,
           "clicks": {"type": "integer", "description": "1 = single, 2 = double, 3 = triple"},
           "modifiers": MODIFIERS},
          ["id"]),
    _tool("click_xy", "Click a point on the screenshot (its pixel space). Use only when the target has no id.",
          {"x": {"type": "integer"}, "y": {"type": "integer"}, "button": BUTTON,
           "clicks": {"type": "integer"}, "modifiers": MODIFIERS},
          ["x", "y"]),
    _tool("type_text", "Type text at the keyboard focus. With `id`, click that element first. "
                       "`clear` selects all existing text first; `submit` presses Enter after.",
          {"text": {"type": "string"}, "id": {"type": "integer"},
           "clear": {"type": "boolean"}, "submit": {"type": "boolean"}},
          ["text"]),
    _tool("key", "Press a key or combination, e.g. 'enter', 'esc', 'tab', 'ctrl+s', 'alt+tab', 'win+r'.",
          {"combo": {"type": "string"}, "repeat": {"type": "integer"}},
          ["combo"]),
    _tool("scroll", "Scroll the element `id` (or the point x,y, or wherever the mouse is).",
          {"direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
           "amount": {"type": "integer", "description": "Wheel notches, usually 3-10."},
           "id": {"type": "integer"}, "x": {"type": "integer"}, "y": {"type": "integer"}},
          ["direction"]),
    _tool("drag", "Drag with the left button between two screenshot points.",
          {"x0": {"type": "integer"}, "y0": {"type": "integer"},
           "x1": {"type": "integer"}, "y1": {"type": "integer"}},
          ["x0", "y0", "x1", "y1"]),
    _tool("zoom", "Look closer: returns a sharper crop of a screenshot region (x0,y0,x1,y1) in the next observation.",
          {"x0": {"type": "integer"}, "y0": {"type": "integer"},
           "x1": {"type": "integer"}, "y1": {"type": "integer"}},
          ["x0", "y0", "x1", "y1"]),
    _tool("wait", "Wait for the screen to change (page loads, app starts).",
          {"seconds": {"type": "number"}},
          ["seconds"]),
    _tool("focus_window", "Bring an open window to the front by part of its title.",
          {"title": {"type": "string"}},
          ["title"]),
    _tool("browser_open", "Open a URL in the agent's own Chrome (launches it if needed) and bring it to the "
                          "front. While it is in front, observations show the page and its numbered elements.",
          {"url": {"type": "string"}, "new_tab": {"type": "boolean"}},
          ["url"]),
    _tool("browser_back", "Go back one page in the active browser tab.", {}, []),
    _tool("browser_switch_tab", "Make another browser tab active (index from the Tabs list) and show it.",
          {"index": {"type": "integer"}}, ["index"]),
    _tool("browser_close_tab", "Close the active browser tab.", {}, []),
    _tool("read_page", "Return the visible text of the whole active web page (beyond what the screenshot shows).",
          {"max_chars": {"type": "integer"}}, []),
    _tool("shell","Run PowerShell on this Windows machine; returns exit code, stdout, stderr. The command "
                   "already runs inside PowerShell: do not wrap it in `powershell -Command`. Good for "
                   "launching apps (Start-Process notepad), files, and checking state.",
          {"command": {"type": "string"}, "cwd": {"type": "string"}},
          ["command"]),
    _tool("read_file", "Read a UTF-8 text file.", {"path": {"type": "string"}}, ["path"]),
    _tool("write_file", "Write (or append) UTF-8 text to a file, creating folders.",
          {"path": {"type": "string"}, "content": {"type": "string"}, "append": {"type": "boolean"}},
          ["path", "content"]),
    _tool("list_dir", "List a folder's entries.", {"path": {"type": "string"}}, ["path"]),
    _tool("ask_human", "Pause and ask the human, then resume with their answer. Only when stuck after retrying, "
                       "at a judgement call the task does not settle, or for approval such as a payment.",
          {"question": {"type": "string"},
           "kind": {"type": "string", "enum": ["stuck", "judgement", "approval"]}},
          ["question", "kind"]),
    _tool("task_complete", "Finish the run, only after verifying the outcome on screen or on disk.",
          {"status": {"type": "string", "enum": ["success", "failed"]},
           "summary": {"type": "string"},
           "evidence": {"type": "string", "description": "How you verified the result."}},
          ["status", "summary", "evidence"]),
]

# Tools that change the screen: after them the harness waits briefly before observing.
SCREEN_CHANGING = {"click", "click_xy", "type_text", "key", "scroll", "drag", "focus_window", "shell",
                   "browser_open", "browser_back", "browser_switch_tab", "browser_close_tab"}
