# Requirements → implementation

Every line of the problem statement is mandatory. Status: ☐ not started · ◐ in progress · ☑ done.

## Harness

| # | Requirement | Implementation | Status |
|---|---|---|---|
| H1 | One general-purpose agent: terminal, files, browser, desktop apps | Single process, one tool registry, no task-specific code | ☐ |
| H2 | New task = new skill, not new code | Task knowledge lives only in `skills/` | ☐ |
| H3 | Hands: shell, file read/write, browser control, mouse + keyboard on any desktop app | `agent/tools/` | ◐ |
| H4 | Eyes: screenshots + any structure (page DOM, terminal output) | `agent/eyes/` | ◐ |
| H5 | Loop: plan, act, observe, verify, repeat until done or needs help | `agent/loop.py` | ◐ |
| H6 | Skills: a folder of skill files | `skills/<name>/SKILL.md` | ☐ |
| H7 | Triggers: chat command, schedule ("at 10:59:30"), event ("when an email arrives") | `agent/triggers/` | ☐ |
| H8 | Human handoff: stuck / judgement call / payment → ask via own channel → resume | `agent/handoff.py` (Telegram) | ◐ |
| H9 | Run log: every step with screenshot + reason, replayable by judges | `agent/runlog.py` → `runs/<id>/replay.html` | ◐ |

## Bonus

| # | Requirement | Implementation | Status |
|---|---|---|---|
| B1 | Self-improving skills: writes what it learned back into the skill | Post-run reflection updates `SKILL.md` / `lessons.md` | ☐ |
| B2 | Skills from a demo: shown or told a task once, drafts its own skill | Record screen + input events, or a told description → drafted `SKILL.md` | ☐ |

## The task

| # | Requirement | Implementation | Status |
|---|---|---|---|
| T1 | Cheapest Tatkal ticket Roorkee → Haridwar, next day, one team member | Fare-ranking rule in `skills/irctc-tatkal/` | ☐ |
| T2 | Hands-free, run scheduled before code freeze | Scheduler armed before 09:00 | ☐ |
| T3 | Log in, find train, book, save proof | IRCTC skill | ☐ |
| T4 | Only human action: approve the final payment | UPI collect request approved on phone | ☐ |
| T5 | Passenger details pre-filled where the agent can read them | `data/passenger.json` | ☐ |
| T6 | Tatkal window opens 11:00 sharp | NTP-synced clock, ready at 10:59:30 | ☐ |
| T7 | Retry and recover at every step (slowdowns, errors, bot checks, rate limits) | Per-step retry, reload/re-login recovery, model fallback, own hotspot | ☐ |

## Submission (Google Drive folder)

| # | Item | Status |
|---|---|---|
| S1 | Submission form (shared by organizers) | ☐ |
| S2 | Demo video: architecture + what the agent can do | ☐ |
| S3 | Run log | ☐ |
| S4 | Screenshot of the ticket with PNR | ☐ |
| S5 | Screen recording of the whole booking, start to finish | ☐ |
| S6 | Phone video of the untouched device for the same period | ☐ |
| S7 | The IRCTC skills the agent wrote | ☐ |

## Terms

| # | Term | Status |
|---|---|---|
| C1 | Cheapest Tatkal RK → HW with correct passenger details | ☐ |
| C2 | No human intervention except approving the final payment | ☐ |
| C3 | Generic agent: not browser-only, not built just for IRCTC | ☐ |
| C4 | One booking per team, from a team member's own IRCTC account | ☐ |
| C5 | No code changes after 09:00 on Oct 25; only the Drive folder may change | ☐ |

## Build order

| Phase | Build | Done when |
|---|---|---|
| 0 | Prerequisites: Aadhaar-verified IRCTC account, passenger in Master List, UPI, Telegram bot, Gmail app password, API keys, OBS, Drive folder, organizer questions | All checked |
| 1 | Core loop + hands + eyes | Agent opens Notepad, writes a line, saves it, unaided |
| 2 | Run log + replay | Phase 1 run replays step by step |
| 3 | Browser | Agent searches the web and saves a result to a file |
| 4 | Skills system + fast-path runner | Same tasks run from skill files, no code change |
| 5 | Human handoff | Agent pauses, asks on Telegram, resumes |
| 6 | Triggers | Chat, schedule and email all start a run |
| 7 | IRCTC skill | Dry runs reach the payment page reliably |
| 8 | Proof pipeline | Scheduled run produces a complete Drive folder |
| 9 | Bonuses | Skill drafted from one demo runs successfully |
| 10 | Generality | Unseen desktop tasks succeed without code changes |
| 11 | Rehearsals | Oct 24 11:00 live window (no payment) + overnight scheduled run |
| 12 | Freeze | Demo video, git tag, schedule armed by 08:59 Oct 25 |

## Open questions for organizers

1. Does entering a captcha or OTP count as an intervention, and what does it cost?
2. What exactly are the "two windows"?
3. Does a waitlisted Tatkal ticket qualify, or only confirmed?
4. Is "cheapest" judged on base fare or total including fees?
