# Skills

Each job the agent can do is one folder here. Adding a job means adding a folder, never editing `agent/`.

```
skills/<name>/
  SKILL.md     required: what the job is, when to use it, how to do it, how to recover
  steps.yaml   optional: fast path the harness replays with no model calls, checking each step
  lessons.md   written by the agent after runs (self-improvement)
```

The harness lists every skill in the prompt, auto-loads the best match for the task (by
`triggers` and description words), and the model can `load_skill` others or `run_skill_steps`.

## SKILL.md

```markdown
---
name: notepad-write
description: Write text in Windows Notepad and save it to a given file path.
triggers: [notepad, write a note]
params: [text, path]          # values the fast path needs
---

## Goal
## Fast path
## Doing it by hand
## Verify
## Known failures and recovery
## When to ask a human
```

## steps.yaml

```yaml
steps:
  - do: browser_open                       # any action tool
    url: https://example.com/form
    expect: {element: {name: "Customer name"}}   # checked after acting, polled until timeout
    timeout: 20
    retries: 1                             # repeat the action if expect fails
  - do: type_text
    target: {role: "input:text", name: "Customer name"}   # found on the current screen
    text: "{name}"                         # from params
    clear: true
  - if: {window: "Confirm Save As"}        # run only when this holds
    do: key
    combo: alt+y
  - wait_for: {url: "/done", text: ["Thank you", "Success"]}
    timeout: 30
  - do: click
    target: {role: button, name: "Next"}
    optional: true                         # failure does not stop the run
```

- **Conditions** (all listed must hold): `window`, `url`, `text` (substring; a list means any
  of), `element`, `no_element` (a target).
- **Targets** match `role` (prefix, e.g. `input` or `Button`), `name`, `value` (case-insensitive
  substrings) and pick the `index`-th match.
- **Values**: `{param}`, `{data.passenger.name}` (from `data/*.json`), `{today+1:%d-%m-%Y}`,
  filters `{path|basename}`, `|stem`, `|upper`, `|lower`; `{{` / `}}` for literal braces.
- If a required step fails, the run stops there and the model continues from the current screen.
