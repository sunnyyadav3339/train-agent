# Skills

Each job the agent can do is one folder here. Adding a job means adding a folder, never editing `agent/`.

```
skills/<name>/
  SKILL.md     required: what the job is, when to use it, how to do it, how to recover
  steps.yaml   optional: fast-path action list the harness replays, verifying each step
  lessons.md   written by the agent after runs (self-improvement)
```

`SKILL.md` starts with frontmatter the loader uses to match a task to a skill:

```markdown
---
name: notepad-write
description: Open Notepad, type given text and save it to a given path.
triggers: [notepad, write a note, save text]
---

## Goal
## Preconditions
## Steps
## Verify
## Known failures and recovery
## When to ask a human
```
