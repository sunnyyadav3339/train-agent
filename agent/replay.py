"""Replay page: turns a run folder (log.jsonl + screenshots) into one self-contained replay.html.

Judges open it in any browser and step through the run: what the agent saw, why it acted,
what it did and what came back, including model switches, handoffs and the final result.

    python -m agent.replay runs/<run_id>            # standalone file, images embedded
    python -m agent.replay runs/<run_id> --linked   # lighter file that links the PNGs beside it
"""

from __future__ import annotations

import argparse
import base64
import html
import io
import json
from pathlib import Path

from PIL import Image

JPEG_QUALITY = 72


def _load(run_dir: Path) -> tuple[dict, list[dict]]:
    meta_path = run_dir / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    events = []
    with (run_dir / "log.jsonl").open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return meta, events


def _image_src(run_dir: Path, name: str | None, embed: bool, cache: dict) -> str | None:
    if not name:
        return None
    if not embed:
        return name
    if name not in cache:
        path = run_dir / name
        if not path.exists():
            return None
        img = Image.open(path).convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        cache[name] = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    return cache[name]


def build_frames(run_dir: Path, events: list[dict], embed: bool) -> tuple[list[dict], dict]:
    """One frame per step: the screenshot the agent saw, the model call, the actions and notes.

    Step N acts on the observation taken after step N-1, so each frame pairs them. Router
    events (model switches, waits) are logged before the model event of the step they delay.
    """
    cache: dict = {}
    frames: dict[int, dict] = {}
    pending_notes: list[dict] = []
    last_obs: dict | None = None
    stats = {"actions": 0, "errors": 0, "handoffs": 0, "model_switches": 0, "routes": {}}
    final: dict = {}

    def frame(step: int) -> dict:
        if step not in frames:
            frames[step] = {"step": step, "seen": None, "window": "", "elements": None,
                            "model": None, "actions": [], "notes": [], "after": None}
        return frames[step]

    for e in events:
        kind = e.get("kind")
        if kind == "task":
            last_obs = {"screenshot": e.get("screenshot"), "window": "", "elements": None}
        elif kind == "observe":
            last_obs = {"screenshot": e.get("screenshot"), "window": e.get("window", ""),
                        "elements": e.get("elements")}
            step = e.get("step", 0)
            if step in frames:
                frames[step]["after"] = _image_src(run_dir, e.get("screenshot"), embed, cache)
        elif kind in ("route_failed", "all_routes_busy", "model_retry", "route_disabled"):
            pending_notes.append({"kind": kind, **{k: v for k, v in e.items() if k not in ("ts", "kind")}})
            if kind == "route_failed":
                stats["model_switches"] += 1
        elif kind == "model":
            f = frame(e["step"])
            f["model"] = {"route": e.get("route"), "latency_s": e.get("latency_s"), "text": e.get("text") or ""}
            f["t"] = e.get("t")
            f["notes"].extend(pending_notes)
            pending_notes = []
            if last_obs:
                f["seen"] = _image_src(run_dir, last_obs["screenshot"], embed, cache)
                f["window"] = last_obs["window"]
                f["elements"] = last_obs["elements"]
            route = e.get("route") or "?"
            stats["routes"][route] = stats["routes"].get(route, 0) + 1
        elif kind == "action":
            f = frame(e["step"])
            a = {"tool": e.get("tool"), "input": e.get("input"), "reason": e.get("reason", ""),
                 "result": e.get("result"), "error": e.get("error"), "skipped": bool(e.get("skipped")),
                 "dur_s": e.get("dur_s")}
            f["actions"].append(a)
            stats["actions"] += 1
            if a["error"]:
                stats["errors"] += 1
        elif kind in ("skill_step", "skill_loaded"):
            f = frame(e.get("step", max(frames) if frames else 0))
            f["notes"].append({k: v for k, v in e.items() if k not in ("ts", "t")})
            if kind == "skill_step":
                stats["fast_path_steps"] = stats.get("fast_path_steps", 0) + 1
        elif kind == "handoff":
            f = frame(max(frames) if frames else 0)
            f["notes"].append({"kind": "handoff", "question": e.get("question"),
                               "handoff_kind": e.get("handoff_kind"),
                               "screenshot": _image_src(run_dir, e.get("screenshot"), embed, cache)})
            stats["handoffs"] += 1
        elif kind == "handoff_answer":
            f = frame(max(frames) if frames else 0)
            f["notes"].append({"kind": "handoff_answer", "answer": e.get("answer")})
        elif kind == "final":
            final["screenshot"] = _image_src(run_dir, e.get("screenshot"), embed, cache)
            final["evidence"] = e.get("evidence")
        elif kind == "run_end":
            final["status"] = e.get("status")
            final["summary"] = e.get("summary")
            final["t"] = e.get("t")
    ordered = [frames[k] for k in sorted(frames)]
    stats["final"] = final
    return ordered, stats


def build_replay(run_dir: Path, embed: bool = True) -> Path:
    run_dir = Path(run_dir)
    meta, events = _load(run_dir)
    frames, stats = build_frames(run_dir, events, embed)
    data = {"meta": meta, "frames": frames, "stats": stats}
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    title = f"Run {meta.get('run_id', run_dir.name)}"
    page = TEMPLATE.replace("__TITLE__", html.escape(title)).replace("__DATA__", payload)
    out = run_dir / "replay.html"
    out.write_text(page, encoding="utf-8")
    return out


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root {
  --bg: #f6f7f9; --panel: #ffffff; --ink: #1b1f24; --muted: #5d6673; --line: #dfe3e8;
  --accent: #2f5bd3; --ok: #1f8a4c; --bad: #c43a3a; --warn: #b8740a; --chip: #eef1f6;
  --code: #f1f3f6;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #111418; --panel: #1a1e24; --ink: #e7eaee; --muted: #9aa4b1; --line: #2c323b;
    --accent: #7ea2ff; --ok: #4cc283; --bad: #ff7b7b; --warn: #f0b24a; --chip: #232932;
    --code: #20262e;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink);
  font: 14px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
header { padding: 16px; border-bottom: 1px solid var(--line); background: var(--panel); }
h1 { font-size: 18px; margin: 0 0 6px; }
.task { color: var(--ink); margin: 0 0 10px; max-width: 1100px; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chip { background: var(--chip); border-radius: 999px; padding: 2px 10px; font-size: 12px; color: var(--muted); }
.status { font-weight: 600; }
.status.success { color: var(--ok); } .status.failed, .status.error { color: var(--bad); }
.status.gave_up { color: var(--warn); }
.summary { margin: 10px 0 0; color: var(--muted); max-width: 1100px; }
.bar { position: sticky; top: 0; z-index: 2; display: flex; gap: 8px; align-items: center;
  padding: 10px 16px; background: var(--panel); border-bottom: 1px solid var(--line); flex-wrap: wrap; }
button { background: var(--chip); color: var(--ink); border: 1px solid var(--line); border-radius: 6px;
  padding: 5px 12px; font: inherit; cursor: pointer; }
button:hover { border-color: var(--accent); }
input[type=range] { flex: 1; min-width: 140px; accent-color: var(--accent); }
.pos { color: var(--muted); font-variant-numeric: tabular-nums; min-width: 90px; }
main { display: grid; grid-template-columns: minmax(0, 3fr) minmax(300px, 2fr); gap: 16px; padding: 16px; }
@media (max-width: 900px) { main { grid-template-columns: 1fr; } }
.shot { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 8px; }
.shot img { width: 100%; height: auto; display: block; border-radius: 4px; cursor: zoom-in; }
.shot .cap { color: var(--muted); font-size: 12px; margin: 6px 2px 0; }
.tabs { display: flex; gap: 6px; margin-bottom: 8px; }
.tabs button.on { border-color: var(--accent); color: var(--accent); }
.side { display: flex; flex-direction: column; gap: 12px; min-width: 0; }
.card { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 12px; }
.card h2 { font-size: 13px; margin: 0 0 8px; color: var(--muted); font-weight: 600; text-transform: uppercase; letter-spacing: .04em; }
.act { border-left: 3px solid var(--accent); padding: 4px 0 4px 10px; margin: 0 0 10px; }
.act.err { border-color: var(--bad); } .act.skip { border-color: var(--line); opacity: .7; }
.tool { font-weight: 600; }
.reason { margin: 2px 0; }
pre { background: var(--code); border-radius: 6px; padding: 6px 8px; margin: 4px 0 0; white-space: pre-wrap;
  word-break: break-word; font: 12px/1.45 ui-monospace, Consolas, monospace; max-height: 220px; overflow: auto; }
.note { font-size: 13px; margin: 0 0 6px; }
.note.warn { color: var(--warn); } .note.hand { color: var(--accent); }
.meta { color: var(--muted); font-size: 12px; }
.strip { display: flex; gap: 6px; overflow-x: auto; padding: 0 16px 16px; }
.strip img { height: 64px; border-radius: 4px; border: 2px solid transparent; cursor: pointer; opacity: .75; }
.strip img.on { border-color: var(--accent); opacity: 1; }
.strip .x { height: 64px; min-width: 40px; display: grid; place-items: center; border-radius: 4px;
  border: 2px solid transparent; background: var(--chip); color: var(--muted); cursor: pointer; font-size: 12px; }
.strip .x.on { border-color: var(--accent); }
.strip .bad { outline: 2px solid var(--bad); }
dialog { border: none; padding: 0; background: transparent; max-width: 96vw; }
dialog img { max-width: 96vw; max-height: 92vh; display: block; }
dialog::backdrop { background: rgba(0,0,0,.8); }
</style>
</head>
<body>
<header>
  <h1 id="title"></h1>
  <p class="task" id="task"></p>
  <div class="chips" id="chips"></div>
  <p class="summary" id="summary"></p>
</header>
<div class="bar">
  <button id="prev" title="Previous (Left arrow)">&larr; Prev</button>
  <button id="play" title="Play / pause (Space)">Play</button>
  <button id="next" title="Next (Right arrow)">Next &rarr;</button>
  <input type="range" id="slider" min="0" value="0">
  <span class="pos" id="pos"></span>
</div>
<main>
  <section class="shot">
    <div class="tabs"><button id="tSeen" class="on">What it saw</button><button id="tAfter">After its actions</button></div>
    <img id="img" alt="Screenshot for this step">
    <p class="cap" id="cap"></p>
  </section>
  <section class="side">
    <div class="card"><h2 id="stepTitle"></h2><div id="model" class="meta"></div><div id="notes"></div></div>
    <div class="card"><h2>Actions</h2><div id="actions"></div></div>
  </section>
</main>
<div class="strip" id="strip"></div>
<dialog id="zoom"><img id="zoomImg" alt="Full-size screenshot"></dialog>
<script type="application/json" id="data">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
const M = D.meta || {}, S = D.stats || {}, F = S.final || {};
const frames = D.frames.slice();
frames.push({ step: 'end', final: true, seen: F.screenshot, actions: [], notes: [] });
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const fmt = v => typeof v === 'string' ? v : JSON.stringify(v, null, 1);

$('title').textContent = `Run ${M.run_id || ''}`;
$('task').textContent = M.task || '';
const status = F.status || M.status || 'unknown';
const chips = [
  `<span class="chip status ${esc(status)}">${esc(status.toUpperCase())}</span>`,
  `<span class="chip">${frames.length - 1} steps</span>`,
  `<span class="chip">${S.actions || 0} actions</span>`,
  M.duration_s != null ? `<span class="chip">${M.duration_s}s</span>` : '',
  `<span class="chip">${S.handoffs || 0} human handoffs</span>`,
  S.errors ? `<span class="chip">${S.errors} failed actions</span>` : '',
  S.model_switches ? `<span class="chip">${S.model_switches} model switches</span>` : '',
  S.fast_path_steps ? `<span class="chip">${S.fast_path_steps} fast-path steps (no model)</span>` : '',
  M.skill_selected ? `<span class="chip">skill: ${esc(M.skill_selected)}</span>` : '',
  ...Object.entries(S.routes || {}).map(([r, n]) => `<span class="chip">${esc(r)} &times;${n}</span>`),
  M.started ? `<span class="chip">started ${esc(M.started.replace('T', ' ').slice(0, 19))}</span>` : '',
].join('');
$('chips').innerHTML = chips;
$('summary').textContent = (F.summary || M.summary || '') + (F.evidence ? `  Evidence: ${F.evidence}` : '');

let i = 0, view = 'seen', timer = null;
function render() {
  const f = frames[i];
  const hasAfter = !f.final && f.after;
  if (!hasAfter) view = 'seen';
  $('tAfter').disabled = !hasAfter;
  $('tSeen').classList.toggle('on', view === 'seen');
  $('tAfter').classList.toggle('on', view === 'after');
  const src = view === 'after' ? f.after : f.seen;
  $('img').src = src || '';
  $('img').style.display = src ? 'block' : 'none';
  $('cap').textContent = f.final ? 'Final screen' :
    view === 'seen' ? `Before step ${f.step}: ${f.window ? `"${f.window}"` : 'start'}${f.elements != null ? `, ${f.elements} numbered elements` : ''}` :
    `After step ${f.step}`;
  $('stepTitle').textContent = f.final ? 'Result' : `Step ${f.step}`;
  if (f.final) {
    $('model').innerHTML = `<span class="status ${esc(status)}">${esc(status.toUpperCase())}</span>`;
    $('notes').innerHTML = `<p class="note">${esc(F.summary || '')}</p>` +
      (F.evidence ? `<p class="note"><b>Evidence:</b> ${esc(F.evidence)}</p>` : '');
    $('actions').innerHTML = '<p class="meta">Run finished.</p>';
  } else {
    const m = f.model || {};
    $('model').innerHTML = `Model <b>${esc(m.route || '?')}</b> answered in ${esc(m.latency_s)}s` +
      (f.t != null ? ` &middot; t+${Math.round(f.t)}s` : '') +
      (m.text ? `<p class="note">${esc(m.text)}</p>` : '');
    $('notes').innerHTML = (f.notes || []).map(n => {
      if (n.kind === 'route_failed') return `<p class="note warn">&#9888; ${esc(n.route)} failed (${esc(n.status ?? '')})${n.exhausted ? ' &ndash; ' + esc(n.exhausted) : ''}: ${esc(n.detail || '')}</p>`;
      if (n.kind === 'all_routes_busy') return `<p class="note warn">&#9203; All models busy, waited ${esc(n.delay_s)}s</p>`;
      if (n.kind === 'handoff') return `<p class="note hand">&#128100; Asked a human (${esc(n.handoff_kind)}): ${esc(n.question)}</p>` +
        (n.screenshot ? `<img src="${n.screenshot}" style="width:100%;border-radius:4px" alt="Screen at handoff">` : '');
      if (n.kind === 'handoff_answer') return `<p class="note hand">&#8617; Human answered: ${esc(n.answer)}</p>`;
      if (n.kind === 'skill_loaded') return `<p class="note">&#128218; Loaded skill ${esc(n.skill)}</p>`;
      if (n.kind === 'skill_step') return `<p class="note${n.error ? ' warn' : ''}">&#9889; Fast path ${esc(n.skill)} #${esc(n.index)}` +
        `${n.note ? ' (' + esc(n.note) + ')' : ''}: ${esc(n.error || n.result || '')}</p>`;
      return `<p class="note">${esc(n.kind)} ${esc(JSON.stringify(n))}</p>`;
    }).join('');
    $('actions').innerHTML = (f.actions || []).map(a => {
      const cls = a.error ? 'act err' : a.skipped ? 'act skip' : 'act';
      const input = a.input && Object.keys(a.input).length ? `<pre>${esc(fmt(a.input))}</pre>` : '';
      const out = a.error ? `<pre>${esc(a.error)}</pre>` : a.skipped ? '<p class="meta">Skipped: an earlier action failed.</p>' :
        a.result ? `<pre>${esc(a.result)}</pre>` : '';
      return `<div class="${cls}"><span class="tool">${esc(a.tool)}</span>` +
        (a.dur_s != null ? ` <span class="meta">${esc(a.dur_s)}s</span>` : '') +
        (a.reason ? `<p class="reason">${esc(a.reason)}</p>` : '') + input + out + '</div>';
    }).join('') || '<p class="meta">No actions.</p>';
  }
  $('slider').value = i;
  $('pos').textContent = `${i + 1} / ${frames.length}`;
  document.querySelectorAll('#strip > *').forEach((el, k) => el.classList.toggle('on', k === i));
  const on = document.querySelector('#strip > .on');
  if (on) on.scrollIntoView({ block: 'nearest', inline: 'center' });
}
function go(k) { i = Math.max(0, Math.min(frames.length - 1, k)); view = 'seen'; render(); }
function play() {
  if (timer) { clearInterval(timer); timer = null; $('play').textContent = 'Play'; return; }
  $('play').textContent = 'Pause';
  timer = setInterval(() => { if (i >= frames.length - 1) play(); else go(i + 1); }, 1800);
}
$('slider').max = frames.length - 1;
$('slider').oninput = e => go(+e.target.value);
$('prev').onclick = () => go(i - 1);
$('next').onclick = () => go(i + 1);
$('play').onclick = play;
$('tSeen').onclick = () => { view = 'seen'; render(); };
$('tAfter').onclick = () => { view = 'after'; render(); };
$('img').onclick = () => { $('zoomImg').src = $('img').src; $('zoom').showModal(); };
$('zoom').onclick = () => $('zoom').close();
document.addEventListener('keydown', e => {
  if (e.target.tagName === 'INPUT') return;
  if (e.key === 'ArrowRight') go(i + 1);
  else if (e.key === 'ArrowLeft') go(i - 1);
  else if (e.key === ' ') { e.preventDefault(); play(); }
});
$('strip').innerHTML = frames.map((f, k) => {
  const bad = (f.actions || []).some(a => a.error) ? ' bad' : '';
  return f.seen ? `<img src="${f.seen}" class="${bad}" title="${f.final ? 'Result' : 'Step ' + f.step}" alt="" onclick="go(${k})">`
                : `<div class="x${bad}" onclick="go(${k})">${f.final ? 'End' : f.step}</div>`;
}).join('');
render();
</script>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(prog="agent.replay", description="Build replay.html for a run folder.")
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--linked", action="store_true", help="Link PNGs beside the page instead of embedding.")
    args = parser.parse_args()
    out = build_replay(args.run_dir, embed=not args.linked)
    print(f"Wrote {out} ({out.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
