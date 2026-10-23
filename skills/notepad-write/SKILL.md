---
name: notepad-write
description: Write text in Windows Notepad and save it to a given file path (Save As).
triggers: [notepad, write a note, save text in notepad]
params: [text, path]
---

## Goal
The text is typed into Notepad (not written from the shell) and saved at the exact path asked for.

## Fast path
Run `run_skill_steps` with `text` and the full `path` (folder + file name). It opens Notepad,
starts a new tab, types the text, opens Save As, enters the full path and saves, replacing an
existing file if Windows asks.

## Doing it by hand
1. `shell`: `Start-Process notepad.exe`, then wait for a window whose title ends in "Notepad".
2. `ctrl+n` for a fresh tab (Notepad reopens old tabs), then type the text.
3. `ctrl+shift+s` opens "Save as". Type the full path into the "File name" box and press Enter.
4. If "Confirm Save As" appears, choose Yes.

## Verify
The window title shows the file name without a leading `*`, and `read_file` on the path returns
the text.

## Known failures and recovery
- `Start-Process notepad` sometimes opens "Select an app to open": pick Notepad in that list.
- Other Notepad windows may already be open (earlier files). `focus_window` " - Notepad" before
  `ctrl+n`, then check the title says "Untitled" before typing; never type into a tab that
  already holds another file.
- If typing went somewhere else, the wrong-window guard stops it: `focus_window` "Notepad" first.
- Closing: `ctrl+w` closes the tab, `alt+f4` closes Notepad. Only close when the task says so.

## When to ask a human
Never needed for this task.
