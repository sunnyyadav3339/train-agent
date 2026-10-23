# train-agent

A general-purpose computer-use agent built for **Syntax Error 2026 — Alum Track Dev I: "Do You Know How to Train?"**

One generic harness, many skills. The harness sees the screen, decides, acts, checks the result and loops until the
task is done or it needs a human. A new task needs a new skill, not new code. The final exam: book the cheapest
Tatkal ticket Roorkee → Haridwar the second the 11:00 window opens, hands-free except for approving the payment.

See [REQUIREMENTS.md](REQUIREMENTS.md) for the full requirement → implementation map and the build order.

## Architecture

```
 Triggers ─┐   chat (Telegram) · schedule (APScheduler) · event (IMAP "when an email arrives")
           ▼
 ┌──────────────── HARNESS (generic, no task-specific code) ────────┐
 │  Skill loader → picks skills/<name>/SKILL.md by description      │
 │  Loop:  PLAN → ACT → OBSERVE → VERIFY → (retry / recover / ask)  │
 │  Model router: primary + fallback, backoff on rate limits        │
 ├──────────── HANDS ───────────┬──────────── EYES ─────────────────┤
 │ shell, file read/write       │ screenshots                       │
 │ browser (Playwright, Chrome) │ numbered elements (DOM, UIA tree) │
 │ mouse + keyboard (desktop)   │ Windows UI Automation tree        │
 │                              │ terminal output                   │
 └──────────────────────────────┴───────────────────────────────────┘
   Human handoff: Telegram (screenshot + question) → pause → resume
   Run log: every step → JSONL + screenshot + reason → replay.html
   After a run: reflection writes lessons back into the skill
```

## Layout

```
agent/
  loop.py          plan → act → observe → verify state machine
  models.py        model router (primary + fallback)
  skills.py        skill discovery, matching, steps.yaml runner
  runlog.py        per-step JSONL + screenshots + replay.html
  handoff.py       pause / ask a human / resume
  tools/           hands: shell, fs, browser, desktop
  eyes/            screen capture, DOM, UI Automation tree
  triggers/        chat, schedule, email
skills/            one folder per job (SKILL.md, steps.yaml, lessons.md)
data/              local inputs (passenger.json is git-ignored)
runs/              run logs (git-ignored)
```

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
copy .env.example .env
copy data\passenger.example.json data\passenger.json
```

Fill in `.env` and `data/passenger.json` locally. Neither is committed.

## Models (free)

Any provider with an OpenAI-compatible chat API works; requests go straight to that provider.
Default: **Gemini** (free key from aistudio.google.com) as primary, **Groq** as fallback.
Switching provider or model is a `.env` change (`PRIMARY_PROVIDER`, `PRIMARY_MODEL`, ...).

For websites the agent drives its **own visible Chrome** (Playwright, separate profile in
`data/chrome-profile/`, so logins persist between runs). While that window is in front, the eyes
read the page DOM and the hands act inside the page in page pixels; downloads land in
`data/downloads/`. Everything else goes through the desktop path.

The agent clicks by number, not by pixel: the eyes read the UI structure, number every element,
and draw the numbers on the screenshot (Set-of-Marks). The model answers `click(17)`, which keeps
free models accurate and lets text-only models act from the element list alone.

## Human handoff (Telegram)

When the agent is stuck, faces a judgement call, or needs a payment approved, it pauses and asks
you on Telegram with a screenshot (approvals get Approve / Reject buttons), then resumes with your
answer. It only reads messages from your chat; with no answer it is told so and never assumes yes.
Each run also sends a start message and, at the end, the final screenshot plus `replay.html`.

1. In Telegram, message **@BotFather** → `/newbot` → copy the token into `.env` as `TELEGRAM_BOT_TOKEN`.
2. `python -m agent.channels.telegram setup`, then send your bot any message: it prints your chat id
   → put it in `.env` as `TELEGRAM_CHAT_ID`.
3. `python -m agent.channels.telegram test` sends a question with buttons and waits for your tap.

Without these two values the agent asks in the console instead.

## Run

```bash
python -m tests.smoke_offline
python -m agent "Open Notepad, type 'hello from the agent', and save it to the Desktop as agent_test.txt"
```

Each run writes `runs/<timestamp>/`: `log.jsonl` (every action with its reason, timing and result), a PNG
for every screenshot, `meta.json` with the final status, and **`replay.html`**: one self-contained page
(screenshots embedded) to step through the run with Prev/Next, arrow keys or Play. Rebuild one with
`python -m agent.replay runs/<timestamp>`. Move the mouse into a screen corner to abort a run.
