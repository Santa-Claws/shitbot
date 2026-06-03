import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import requests
from flask import Flask, jsonify, redirect, render_template_string, request, url_for

APP = Flask(__name__)

DB_PATH = Path(os.getenv("SHITPOST_REGISTRY_PATH", "/data/repost_registry.sqlite"))
SUBS_PATH = Path("/data/subreddits.json")
MEDIA_DIR = Path("/media")
WORKER_URL = os.getenv("SHITPOST_SERVICE_URL", "http://shitpost-worker:8000").rstrip("/")
SCHEDULER_URL = os.getenv("SHITPOST_SCHEDULER_URL", "http://shitpost-scheduler:8001").rstrip("/")

DEFAULT_SUBS = [
    {"name": "MemeVideos", "weight": 1.0},
    {"name": "MurderedByWords", "weight": 1.0},
    {"name": "blursed_videos", "weight": 1.0},
    {"name": "Kitchencels", "weight": 1.0},
    {"name": "addressme", "weight": 1.0},
    {"name": "CursedGuns", "weight": 1.0},
    {"name": "perfectlycutvideos", "weight": 1.0},
]

STATUS_COLORS = {
    "posted": "green", "downloaded": "blue", "attempt_started": "grey",
    "duplicate_hash": "orange", "already_known": "orange",
    "download_failed": "red", "download_timeout": "red",
    "unsupported_media": "yellow", "image_too_large": "yellow",
    "media_too_large": "yellow", "read_failed": "red", "discord_failed": "red",
    "cleanup": "grey",
}

NAV = """
<nav>
  <ul><li><strong>🤖 Shitbot</strong></li></ul>
  <ul>
    <li><a href="/" {da}>Dashboard</a></li>
    <li><a href="/subreddits" {sa}>Subreddits</a></li>
    <li><a href="/history" {ha}>History</a></li>
    <li><a href="/errors" {ea}>Errors</a></li>
    <li><a href="/stats" {sta}>Stats</a></li>
    <li><a href="/feed-preview" {fa}>Feed Preview</a></li>
    <li><a href="/scheduling" {sca}>Scheduling</a></li>
  </ul>
</nav>
"""

CSS = """
<style>
  nav{padding:.5rem 1rem}
  .al{color:var(--pico-primary)!important}
  .stat-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(155px,1fr));gap:1rem;margin-bottom:1.5rem}
  .stat-card{background:var(--pico-card-background-color);border:1px solid var(--pico-card-border-color);border-radius:var(--pico-border-radius);padding:1rem 1.25rem}
  .stat-card .num{font-size:2rem;font-weight:700;line-height:1;margin-bottom:.25rem}
  .stat-card .lbl{font-size:.8rem;color:var(--pico-muted-color);text-transform:uppercase;letter-spacing:.05em}
  .badge{display:inline-block;padding:.15em .5em;border-radius:999px;font-size:.75rem;font-weight:600}
  .bg{background:#1a3d1a;color:#4caf50} .br{background:#3d1a1a;color:#f44336}
  .bo{background:#3d2a1a;color:#ff9800} .bb{background:#1a2a3d;color:#2196f3}
  .by{background:#3d3a1a;color:#ffeb3b} .bgr{background:#2a2a2a;color:#9e9e9e}
  .hdot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px}
  .hok{background:#4caf50} .herr{background:#f44336}
  .cw{position:relative;max-height:280px}
  .ph{display:flex;align-items:center;justify-content:space-between;margin-bottom:1rem}
  .mu{color:var(--pico-muted-color);font-size:.85rem}
  table{font-size:.88rem}
</style>
"""

HEAD = """<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{title} — Shitbot Manager</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.min.css">
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
  {css}
</head>
<body>
{nav}
<main class="container">
"""

FOOT = "\n</main></body></html>"


def a(flag: bool) -> str:
    return 'class="al"' if flag else ""


def layout(body: str, active: str = "", title: str = "Shitbot") -> str:
    nav = NAV.format(
        da=a(active == "d"), sa=a(active == "s"), ha=a(active == "h"),
        ea=a(active == "e"), sta=a(active == "st"), fa=a(active == "f"),
        sca=a(active == "sc"),
    )
    return HEAD.format(title=title, css=CSS, nav=nav) + body + FOOT


def db():
    if not DB_PATH.exists():
        return None
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def load_subs() -> list[dict]:
    if SUBS_PATH.exists():
        try:
            return json.loads(SUBS_PATH.read_text())
        except Exception:
            pass
    return list(DEFAULT_SUBS)


def save_subs(subs: list[dict]) -> None:
    SUBS_PATH.write_text(json.dumps(subs, indent=2))


def badge(status: str) -> str:
    c = {"green": "bg", "red": "br", "orange": "bo", "blue": "bb",
         "yellow": "by", "grey": "bgr"}.get(STATUS_COLORS.get(status, "grey"), "bgr")
    return f'<span class="badge {c}">{status}</span>'


def fsize(n) -> str:
    if not n:
        return "—"
    n = int(n)
    return f"{n/1_000_000:.1f} MB" if n >= 1_000_000 else f"{n/1_000:.0f} KB"


def worker_health() -> bool:
    try:
        return requests.get(f"{WORKER_URL}/healthz", timeout=3).ok
    except Exception:
        return False


def scheduler_health() -> bool:
    try:
        return requests.get(f"{SCHEDULER_URL}/healthz", timeout=3).ok
    except Exception:
        return False


def scheduler_status() -> dict | None:
    try:
        r = requests.get(f"{SCHEDULER_URL}/api/status", timeout=5)
        return r.json() if r.ok else None
    except Exception:
        return None


def scheduler_config() -> dict | None:
    try:
        r = requests.get(f"{SCHEDULER_URL}/api/config", timeout=5)
        return r.json() if r.ok else None
    except Exception:
        return None


def media_usage() -> tuple[int, int]:
    total, count = 0, 0
    if MEDIA_DIR.exists():
        for f in MEDIA_DIR.iterdir():
            if f.is_file():
                total += f.stat().st_size
                count += 1
    return total, count


# ── Dashboard ────────────────────────────────────────────────────────────────

@APP.get("/")
def dashboard():
    conn = db()
    stats = {"total": 0, "posted": 0, "today": 0, "success_rate": 0}
    recent, status_counts = [], {}
    if conn:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        stats["total"] = conn.execute("SELECT COUNT(*) FROM repost_registry").fetchone()[0]
        stats["posted"] = conn.execute("SELECT COUNT(*) FROM repost_registry WHERE status='posted'").fetchone()[0]
        stats["today"] = conn.execute(
            "SELECT COUNT(*) FROM repost_registry WHERE status='posted' AND DATE(finalized_at)=?", (today,)
        ).fetchone()[0]
        stats["success_rate"] = round(stats["posted"] / stats["total"] * 100) if stats["total"] else 0
        rows = conn.execute("SELECT status, COUNT(*) n FROM repost_registry GROUP BY status ORDER BY n DESC").fetchall()
        status_counts = {r["status"]: r["n"] for r in rows}
        recent = conn.execute(
            "SELECT subreddit, title, canonical_post_url, mime_type, finalized_at "
            "FROM repost_registry WHERE status='posted' ORDER BY finalized_at DESC LIMIT 10"
        ).fetchall()
        conn.close()

    wok = worker_health()
    sched = scheduler_status()
    sok = sched is not None
    sched_state = (sched or {}).get("state", "unknown")
    sched_next = ((sched or {}).get("next_run") or "")[:16]
    sched_last = ((sched or {}).get("last_result") or "—")
    dbytes, dcount = media_usage()
    sc_labels = json.dumps(list(status_counts.keys()))
    sc_values = json.dumps(list(status_counts.values()))
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    recent_rows = "".join(
        f"<tr><td>r/{r['subreddit']}</td>"
        f"<td><a href='{r['canonical_post_url']}' target='_blank' rel='noopener'>{(r['title'] or '')[:70]}{'…' if r['title'] and len(r['title'])>70 else ''}</a></td>"
        f"<td>{r['mime_type'] or '—'}</td>"
        f"<td class='mu'>{(r['finalized_at'] or '')[:16]}</td></tr>"
        for r in recent
    )

    body = f"""
<div class="ph"><h2>Dashboard</h2><span class="mu">{now}</span></div>
<div class="stat-grid">
  <div class="stat-card"><div class="num">{stats['total']}</div><div class="lbl">Total Attempts</div></div>
  <div class="stat-card"><div class="num" style="color:#4caf50">{stats['posted']}</div><div class="lbl">Posted</div></div>
  <div class="stat-card"><div class="num">{stats['today']}</div><div class="lbl">Posted Today</div></div>
  <div class="stat-card"><div class="num">{stats['success_rate']}%</div><div class="lbl">Success Rate</div></div>
  <div class="stat-card"><div class="num">{round(dbytes/1_000_000,1)}</div><div class="lbl">Media MB ({dcount} files)</div></div>
</div>
<div style="display:grid;grid-template-columns:1fr 1fr;gap:1rem;margin-bottom:1.5rem">
  <article>
    <header><strong>System Health</strong></header>
    <p><span class="hdot {'hok' if wok else 'herr'}"></span>shitpost-worker {'online' if wok else 'offline'}</p>
    <p><span class="hdot {'hok' if sok else 'herr'}"></span>scheduler {'online' if sok else 'offline'}</p>
    <p class="mu" style="font-size:.85rem;margin-top:.5rem">State: <strong>{sched_state}</strong> · Last: {sched_last} · Next: {sched_next or '—'}</p>
  </article>
  <article>
    <header><strong>Status Breakdown</strong></header>
    <div class="cw"><canvas id="sc"></canvas></div>
  </article>
</div>
<article>
  <header><strong>Recently Posted</strong></header>
  {'<table><thead><tr><th>Subreddit</th><th>Title</th><th>Type</th><th>Time</th></tr></thead><tbody>' + recent_rows + '</tbody></table>' if recent else '<p class="mu">No posts yet.</p>'}
</article>
<script>
new Chart(document.getElementById('sc'),{{
  type:'doughnut',
  data:{{labels:{sc_labels},datasets:[{{data:{sc_values},borderWidth:1}}]}},
  options:{{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{position:'right',labels:{{font:{{size:11}}}}}}}}}}
}});
</script>
"""
    return layout(body, active="d", title="Dashboard")


# ── Subreddits ───────────────────────────────────────────────────────────────

@APP.get("/subreddits")
def subreddits_page():
    subs = load_subs()
    conn = db()
    sub_stats = {}
    if conn:
        rows = conn.execute(
            "SELECT subreddit, COUNT(*) total, SUM(CASE WHEN status='posted' THEN 1 ELSE 0 END) posted "
            "FROM repost_registry GROUP BY subreddit"
        ).fetchall()
        sub_stats = {r["subreddit"]: dict(r) for r in rows}
        conn.close()

    def rate(name):
        st = sub_stats.get(name, {})
        t = st.get("total", 0)
        return f"{round(st.get('posted',0)/t*100)}%" if t else "—"

    rows_html = "".join(
        f"<tr>"
        f"<td><a href='https://reddit.com/r/{s['name']}' target='_blank'>r/{s['name']}</a></td>"
        f"<td><form method='post' action='/subreddits/weight' style='display:flex;gap:.3rem;align-items:center;margin:0'>"
        f"<input type='hidden' name='name' value='{s['name']}'>"
        f"<input type='number' name='weight' value='{s['weight']}' min='0.1' max='100' step='0.1' style='width:68px;margin:0;padding:.2rem .4rem'>"
        f"<button type='submit' style='padding:.2rem .6rem;margin:0'>Set</button></form></td>"
        f"<td>{sub_stats.get(s['name'],{}).get('total',0)}</td>"
        f"<td>{sub_stats.get(s['name'],{}).get('posted',0)}</td>"
        f"<td>{rate(s['name'])}</td>"
        f"<td><form method='post' action='/subreddits/remove' onsubmit=\"return confirm('Remove r/{s['name']}?')\" style='margin:0'>"
        f"<input type='hidden' name='name' value='{s['name']}'>"
        f"<button type='submit' class='secondary' style='padding:.2rem .6rem;margin:0'>Remove</button></form></td>"
        f"</tr>"
        for s in subs
    )

    body = f"""
<div class="ph"><h2>Subreddits</h2></div>
<article>
<table>
  <thead><tr><th>Subreddit</th><th>Weight</th><th>Attempts</th><th>Posted</th><th>Success</th><th></th></tr></thead>
  <tbody>{rows_html}</tbody>
</table>
</article>
<article>
  <header><strong>Add Subreddit</strong></header>
  <form method="post" action="/subreddits/add" style="display:flex;gap:.75rem;align-items:flex-end;flex-wrap:wrap">
    <label style="flex:1;min-width:150px">Name<input type="text" name="name" placeholder="subredditname" required></label>
    <label style="width:100px">Weight<input type="number" name="weight" value="1.0" min="0.1" max="100" step="0.1"></label>
    <button type="submit" style="margin-bottom:1px">Add</button>
  </form>
</article>
"""
    return layout(body, active="s", title="Subreddits")


@APP.post("/subreddits/add")
def subreddits_add():
    name = request.form.get("name", "").strip()
    try:
        weight = round(float(request.form.get("weight", 1.0)), 4)
    except ValueError:
        weight = 1.0
    if name:
        subs = load_subs()
        if not any(s["name"].lower() == name.lower() for s in subs):
            subs.append({"name": name, "weight": max(0.1, weight)})
            save_subs(subs)
    return redirect(url_for("subreddits_page"))


@APP.post("/subreddits/remove")
def subreddits_remove():
    name = request.form.get("name", "").strip()
    if name:
        save_subs([s for s in load_subs() if s["name"] != name])
    return redirect(url_for("subreddits_page"))


@APP.post("/subreddits/weight")
def subreddits_weight():
    name = request.form.get("name", "").strip()
    try:
        weight = round(float(request.form.get("weight", 1.0)), 4)
    except ValueError:
        weight = 1.0
    if name:
        subs = load_subs()
        for s in subs:
            if s["name"] == name:
                s["weight"] = max(0.1, weight)
        save_subs(subs)
    return redirect(url_for("subreddits_page"))


# ── History ──────────────────────────────────────────────────────────────────

@APP.get("/history")
def history_page():
    page = max(1, int(request.args.get("page", 1)))
    per_page = 50
    sub_f = request.args.get("sub", "")
    status_f = request.args.get("status", "")

    conn = db()
    rows, total, subs, statuses = [], 0, [], []
    if conn:
        subs = [r[0] for r in conn.execute("SELECT DISTINCT subreddit FROM repost_registry ORDER BY subreddit").fetchall()]
        statuses = [r[0] for r in conn.execute("SELECT DISTINCT status FROM repost_registry ORDER BY status").fetchall()]
        where, params = [], []
        if sub_f:
            where.append("subreddit=?"); params.append(sub_f)
        if status_f:
            where.append("status=?"); params.append(status_f)
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        total = conn.execute(f"SELECT COUNT(*) FROM repost_registry {clause}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT id, subreddit, title, canonical_post_url, status, mime_type, media_bytes, "
            f"failure_reason, canonical_media_url, content_hash, created_at, finalized_at "
            f"FROM repost_registry {clause} ORDER BY id DESC LIMIT ? OFFSET ?",
            params + [per_page, (page - 1) * per_page]
        ).fetchall()
        conn.close()

    total_pages = max(1, (total + per_page - 1) // per_page)

    sub_opts = "".join(f"<option value='{s}' {'selected' if sub_f==s else ''}>r/{s}</option>" for s in subs)
    st_opts = "".join(f"<option value='{s}' {'selected' if status_f==s else ''}>{s}</option>" for s in statuses)

    def tr(r):
        title = r['title'] or ''
        detail = ""
        if r['failure_reason']:
            detail += f"<div>⚠ {r['failure_reason'][:200]}</div>"
        if r['canonical_media_url']:
            detail += f"<div>Media: {r['canonical_media_url'][:80]}</div>"
        if r['content_hash']:
            detail += f"<div>Hash: {r['content_hash'][:16]}…</div>"
        ts = (r['finalized_at'] or r['created_at'] or '')[:16]
        return (
            f"<tr><td>{r['id']}</td><td>r/{r['subreddit']}</td>"
            f"<td><details><summary><a href='{r['canonical_post_url']}' target='_blank' rel='noopener'>"
            f"{title[:60]}{'…' if len(title)>60 else ''}</a></summary>"
            f"<small class='mu'>{detail}</small></details></td>"
            f"<td>{badge(r['status'])}</td><td>{r['mime_type'] or '—'}</td>"
            f"<td>{fsize(r['media_bytes'])}</td><td class='mu'>{ts}</td></tr>"
        )

    rows_html = "".join(tr(r) for r in rows)
    qs = f"sub={sub_f}&status={status_f}"
    prev_btn = f"<a href='/history?page={page-1}&{qs}' role='button' class='secondary'>← Prev</a>" if page > 1 else ""
    next_btn = f"<a href='/history?page={page+1}&{qs}' role='button' class='secondary'>Next →</a>" if page < total_pages else ""
    clear_btn = f"<a href='/history' role='button' class='secondary'>Clear</a>" if sub_f or status_f else ""

    body = f"""
<div class="ph"><h2>History</h2><span class="mu">{total} records</span></div>
<form method="get" style="display:flex;gap:.5rem;flex-wrap:wrap;margin-bottom:1rem">
  <select name="sub" style="width:auto"><option value="">All subreddits</option>{sub_opts}</select>
  <select name="status" style="width:auto"><option value="">All statuses</option>{st_opts}</select>
  <button type="submit">Filter</button>{clear_btn}
</form>
<article>
<table>
  <thead><tr><th>#</th><th>Subreddit</th><th>Title</th><th>Status</th><th>Type</th><th>Size</th><th>Time</th></tr></thead>
  <tbody>{rows_html}</tbody>
</table>
</article>
<nav style="display:flex;gap:.5rem;justify-content:center">
  {prev_btn}<span style="padding:.5rem 1rem" class="mu">{page} / {total_pages}</span>{next_btn}
</nav>
"""
    return layout(body, active="h", title="History")


# ── Errors ───────────────────────────────────────────────────────────────────

@APP.get("/errors")
def errors_page():
    conn = db()
    groups, stuck = {}, 0
    if conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) n FROM repost_registry "
            "WHERE status NOT IN ('posted','downloaded','attempt_started') "
            "GROUP BY status ORDER BY n DESC"
        ).fetchall()
        for r in rows:
            ex = conn.execute(
                "SELECT subreddit, title, canonical_post_url, failure_reason, created_at "
                "FROM repost_registry WHERE status=? ORDER BY id DESC LIMIT 5", (r["status"],)
            ).fetchall()
            groups[r["status"]] = {"count": r["n"], "examples": ex}
        stuck = conn.execute(
            "SELECT COUNT(*) FROM repost_registry "
            "WHERE status IN ('attempt_started','downloaded') AND finalized_at IS NULL "
            "AND CAST((julianday('now')-julianday(created_at))*86400 AS INTEGER) > 3600"
        ).fetchone()[0]
        conn.close()

    cleanup_btn = f"<form method='post' action='/errors/cleanup'><button type='submit' class='secondary'>🧹 Clean up {stuck} stuck attempts</button></form>" if stuck > 0 else ""

    sections = ""
    for status, data in groups.items():
        ex_rows = "".join(
            f"<tr><td>r/{e['subreddit']}</td>"
            f"<td><a href='{e['canonical_post_url']}' target='_blank'>{(e['title'] or '')[:50]}{'…' if e['title'] and len(e['title'])>50 else ''}</a></td>"
            f"<td class='mu' style='font-size:.8rem'>{(e['failure_reason'] or '—')[:120]}</td>"
            f"<td class='mu'>{(e['created_at'] or '')[:16]}</td></tr>"
            for e in data["examples"]
        )
        sections += f"""
<article>
  <header style="display:flex;align-items:center;gap:.75rem">{badge(status)} <strong>{data['count']} occurrences</strong></header>
  <table><thead><tr><th>Subreddit</th><th>Title</th><th>Reason</th><th>Time</th></tr></thead>
  <tbody>{ex_rows}</tbody></table>
</article>"""

    body = f"""
<div class="ph"><h2>Error Log</h2>{cleanup_btn}</div>
{"<p class='mu'>No errors recorded.</p>" if not groups else sections}
"""
    return layout(body, active="e", title="Errors")


@APP.post("/errors/cleanup")
def errors_cleanup():
    conn = db()
    if conn:
        conn.execute(
            "UPDATE repost_registry SET status='cleanup', finalized_at=datetime('now'), updated_at=datetime('now') "
            "WHERE status IN ('attempt_started','downloaded') AND finalized_at IS NULL "
            "AND CAST((julianday('now')-julianday(created_at))*86400 AS INTEGER) > 3600"
        )
        conn.commit()
        conn.close()
    return redirect(url_for("errors_page"))


# ── Stats ────────────────────────────────────────────────────────────────────

@APP.get("/stats")
def stats_page():
    conn = db()
    days_l, days_v, sub_l, sub_v, mime_l, mime_v, size_l, size_v = (
        "[]", "[]", "[]", "[]", "[]", "[]", "[]", "[]"
    )
    if conn:
        def jl(rows, key):
            return json.dumps([r[key] for r in rows])
        def jv(rows, key):
            return json.dumps([r[key] for r in rows])

        d = conn.execute(
            "SELECT DATE(finalized_at) day, COUNT(*) n FROM repost_registry "
            "WHERE status='posted' AND finalized_at>=date('now','-30 days') GROUP BY day ORDER BY day"
        ).fetchall()
        days_l, days_v = jl(d, "day"), jv(d, "n")

        s = conn.execute(
            "SELECT subreddit, COUNT(*) n FROM repost_registry WHERE status='posted' "
            "GROUP BY subreddit ORDER BY n DESC LIMIT 15"
        ).fetchall()
        sub_l, sub_v = jl(s, "subreddit"), jv(s, "n")

        m = conn.execute(
            "SELECT CASE WHEN mime_type LIKE 'video%' THEN 'video' "
            "WHEN mime_type LIKE 'image%' THEN 'image' ELSE 'other' END kind, COUNT(*) n "
            "FROM repost_registry WHERE status='posted' GROUP BY kind"
        ).fetchall()
        mime_l, mime_v = jl(m, "kind"), jv(m, "n")

        sz = conn.execute(
            "SELECT subreddit, ROUND(AVG(media_bytes)/1000000.0,2) avg_mb "
            "FROM repost_registry WHERE status='posted' AND media_bytes IS NOT NULL "
            "GROUP BY subreddit ORDER BY avg_mb DESC LIMIT 10"
        ).fetchall()
        size_l, size_v = jl(sz, "subreddit"), jv(sz, "avg_mb")
        conn.close()

    body = f"""
<h2>Stats</h2>
<article>
  <header><strong>Posts per Day (last 30 days)</strong></header>
  <div class="cw"><canvas id="c1"></canvas></div>
</article>
<div style="display:grid;grid-template-columns:1fr 1fr;gap:1rem">
  <article><header><strong>Posts per Subreddit</strong></header><div class="cw"><canvas id="c2"></canvas></div></article>
  <article><header><strong>Media Type</strong></header><div class="cw"><canvas id="c3"></canvas></div></article>
</div>
<article>
  <header><strong>Avg File Size by Subreddit (MB)</strong></header>
  <div class="cw"><canvas id="c4"></canvas></div>
</article>
<script>
const cd={{responsive:true,maintainAspectRatio:false}};
new Chart(document.getElementById('c1'),{{type:'bar',data:{{labels:{days_l},datasets:[{{label:'Posts',data:{days_v},borderRadius:3}}]}},options:{{...cd,plugins:{{legend:{{display:false}}}}}}}});
new Chart(document.getElementById('c2'),{{type:'bar',data:{{labels:{sub_l},datasets:[{{label:'Posts',data:{sub_v},borderRadius:3}}]}},options:{{...cd,indexAxis:'y',plugins:{{legend:{{display:false}}}}}}}});
new Chart(document.getElementById('c3'),{{type:'doughnut',data:{{labels:{mime_l},datasets:[{{data:{mime_v},borderWidth:1}}]}},options:{{...cd,plugins:{{legend:{{position:'bottom'}}}}}}}});
new Chart(document.getElementById('c4'),{{type:'bar',data:{{labels:{size_l},datasets:[{{label:'Avg MB',data:{size_v},borderRadius:3}}]}},options:{{...cd,indexAxis:'y',plugins:{{legend:{{display:false}}}}}}}});
</script>
"""
    return layout(body, active="st", title="Stats")


# ── Feed Preview ──────────────────────────────────────────────────────────────

@APP.get("/feed-preview")
def feed_preview():
    items, errors, fetched_at = [], [], None
    conn = db()
    err_msg = ""

    try:
        r = requests.post(f"{WORKER_URL}/api/feed-pool", json={"subreddits": []}, timeout=30)
        if r.ok:
            data = r.json()
            raw = data.get("items", [])[:60]
            errors = data.get("errors", [])
            fetched_at = data.get("fetchedAt", "")[:16]
            if conn and raw:
                seen = set()
                for row in conn.execute("SELECT canonical_post_id, canonical_post_url FROM repost_registry").fetchall():
                    if row["canonical_post_id"]:
                        seen.add(row["canonical_post_id"])
                    if row["canonical_post_url"]:
                        seen.add(row["canonical_post_url"])
                for item in raw:
                    item["_seen"] = item.get("postId", "") in seen or item.get("permalink", "") in seen
            items = raw
        else:
            err_msg = f"Worker returned {r.status_code}"
    except Exception as exc:
        err_msg = str(exc)

    if conn:
        conn.close()

    new_count = sum(1 for i in items if not i.get("_seen"))

    err_section = ""
    if err_msg:
        err_section = f"<article><p style='color:#f44336'>⚠ {err_msg}</p></article>"
    if errors:
        err_section += "<article><header><strong>Feed Errors</strong></header>" + "".join(
            f"<p class='mu'>r/{e['subreddit']}: {e['error']}</p>" for e in errors
        ) + "</article>"

    def preview_row(i):
        seen_badge = '<span class="badge bgr">seen</span>' if i.get("_seen") else '<span class="badge bg">new</span>'
        opacity = "opacity:.4" if i.get("_seen") else ""
        title = i.get("title", "")
        title_s = title[:65] + ("…" if len(title) > 65 else "")
        media = (i.get("mediaUrl", "") or "")[:40] or "—"
        pub = (i.get("publishedAt", "") or "")[:10] or "—"
        return (
            f"<tr style='{opacity}'>"
            f"<td>r/{i.get('subreddit','')}</td>"
            f"<td><a href='{i.get('permalink','')}' target='_blank' rel='noopener'>{title_s}</a></td>"
            f"<td class='mu' style='font-size:.8rem'>{media}</td>"
            f"<td>{seen_badge}</td>"
            f"<td class='mu'>{pub}</td></tr>"
        )
    rows_html = "".join(preview_row(i) for i in items)

    body = f"""
<div class="ph"><h2>Feed Preview</h2>
  <span class="mu">{new_count} new / {len(items)} fetched{' · ' + fetched_at if fetched_at else ''}</span>
</div>
{err_section}
{"<p class='mu'>No items — worker may be unreachable.</p>" if not items and not err_msg else ""}
{'<article><table><thead><tr><th>Subreddit</th><th>Title</th><th>Media</th><th>Status</th><th>Published</th></tr></thead><tbody>' + rows_html + '</tbody></table></article>' if items else ""}
"""
    return layout(body, active="f", title="Feed Preview")


# ── Trigger ───────────────────────────────────────────────────────────────────

def _trigger_page_html(notice: str = ""):
    sched = scheduler_status()
    state = (sched or {}).get("state", "unknown")
    last_run = ((sched or {}).get("last_run") or "—")[:19]
    next_run = ((sched or {}).get("next_run") or "—")[:19]
    last_result = (sched or {}).get("last_result") or "—"
    last_error = (sched or {}).get("last_error") or ""
    last_post = (sched or {}).get("last_post") or {}

    state_color = {"running": "#2196f3", "idle": "#4caf50", "error": "#f44336"}.get(state, "#9e9e9e")

    last_post_html = ""
    if last_post:
        lp_sub = last_post.get("subreddit", "")
        lp_title = (last_post.get("title") or "")[:80]
        last_post_html = f"<p class='mu'>Last posted: <strong>r/{lp_sub}</strong> — {lp_title}</p>"

    error_html = f"<p style='color:#f44336'>{last_error}</p>" if last_error else ""

    body = f"""
<h2>Manual Trigger</h2>
<article style="text-align:center;padding:2rem">
  <p class="mu">Immediately kick off the Shitpost Bot.</p>
  <form method="post" action="/trigger/run">
    <button type="submit" style="font-size:1.2rem;padding:.75rem 2.5rem" {'disabled' if state == 'running' else ''}>
      {'⏳ Running…' if state == 'running' else '▶ Run Now'}
    </button>
  </form>
  {'<p class="mu" style="margin-top:.5rem">Refresh to check status after triggering.</p>' if state != 'running' else ''}
</article>
{notice}
<article>
  <header><strong>Scheduler Status</strong></header>
  <table>
    <tr><td>State</td><td><span style="color:{state_color};font-weight:600">{state}</span></td></tr>
    <tr><td>Last Run</td><td>{last_run} UTC</td></tr>
    <tr><td>Last Result</td><td>{badge(last_result) if last_result != '—' else '—'}</td></tr>
    <tr><td>Next Scheduled</td><td>{next_run} UTC</td></tr>
  </table>
  {last_post_html}
  {error_html}
</article>
"""
    return layout(body, active="t", title="Run Now")


@APP.get("/trigger")
def trigger_page():
    return _trigger_page_html()


@APP.post("/trigger/run")
def trigger_run():
    notice = ""
    try:
        r = requests.post(f"{SCHEDULER_URL}/api/trigger", timeout=10)
        if r.ok:
            data = r.json()
            if data.get("status") == "already_running":
                notice = "<article><p style='color:#ff9800'>⚠ Scheduler is already running — try again shortly.</p></article>"
            else:
                notice = "<article><p style='color:#4caf50'>✅ Triggered. Refresh in a few seconds to see the result.</p></article>"
        else:
            notice = f"<article><p style='color:#f44336'>⚠ Scheduler returned {r.status_code}: {r.text[:200]}</p></article>"
    except Exception as exc:
        notice = f"<article><p style='color:#f44336'>⚠ {exc}</p></article>"
    return _trigger_page_html(notice=notice)


# ── Scheduling ───────────────────────────────────────────────────────────────

def _scheduling_page_html(notice: str = "") -> str:
    cfg = scheduler_config() or {
        "interval_hours": 4, "posts_per_run": 1,
        "max_failures_per_run": 10, "failure_alert_enabled": True,
        "failure_alert_webhook": "",
    }
    sched = scheduler_status()
    state = (sched or {}).get("state", "unknown")
    next_run = ((sched or {}).get("next_run") or "—")[:19]
    last_result = (sched or {}).get("last_result") or "—"
    last_post = (sched or {}).get("last_post") or {}
    run_stats = (sched or {}).get("last_run_stats") or {}
    posted_n = run_stats.get("posted", 0)
    fail_n = run_stats.get("failures", 0)

    state_color = {"running": "#2196f3", "idle": "#4caf50", "failure_limit_reached": "#f44336"}.get(state, "#9e9e9e")

    iv = cfg.get("interval_hours", 4)
    ppr = cfg.get("posts_per_run", 1)
    mf = cfg.get("max_failures_per_run", 10)
    alert_on = cfg.get("failure_alert_enabled", True)
    alert_wh = cfg.get("failure_alert_webhook", "") or ""

    alert_checked = "checked" if alert_on else ""

    last_post_html = ""
    if last_post:
        lp_sub = last_post.get("subreddit", "")
        lp_title = (last_post.get("title") or "")[:80]
        last_post_html = f"<p class='mu' style='margin-top:.5rem'>Last posted: <strong>r/{lp_sub}</strong> — {lp_title}</p>"

    run_stats_html = ""
    if sched:
        result_badge = badge(last_result) if last_result != "—" else "—"
        run_stats_html = f"<p class='mu' style='margin-top:.25rem'>Last run: {result_badge} · {posted_n} posted · {fail_n} failures</p>"

    body = f"""
<div class="ph"><h2>Scheduling</h2></div>
{notice}
<form method="post" action="/scheduling/save">
<div style="display:grid;grid-template-columns:1fr 1fr;gap:1rem;margin-bottom:1rem">
  <article>
    <header><strong>Schedule</strong></header>
    <label>Interval (hours)
      <input type="number" name="interval_hours" value="{iv}" min="0.1" max="168" step="0.1" required>
    </label>
    <label>Posts per run
      <input type="number" name="posts_per_run" value="{ppr}" min="1" max="50" step="1" required>
      <small class="mu">Bot will keep trying until this many posts succeed each run.</small>
    </label>
  </article>
  <article>
    <header><strong>Failure Failsafe</strong></header>
    <label>Max failures per run
      <input type="number" name="max_failures_per_run" value="{mf}" min="1" max="100" step="1" required>
      <small class="mu">Stop the run early after this many download or post failures.</small>
    </label>
    <label style="display:flex;align-items:center;gap:.5rem;cursor:pointer">
      <input type="checkbox" name="failure_alert_enabled" value="1" {alert_checked} style="width:auto;margin:0">
      Send alert when failure limit is hit
    </label>
    <label style="margin-top:.5rem">Alert webhook URL
      <input type="url" name="failure_alert_webhook" value="{alert_wh}" placeholder="https://discord.com/api/webhooks/…">
      <small class="mu">Leave blank to use the main Discord webhook. Can be a different channel.</small>
    </label>
  </article>
</div>
<button type="submit">Save Settings</button>
</form>

<article style="margin-top:1.5rem">
  <header><strong>Manual Run</strong></header>
  <form method="post" action="/scheduling/trigger" style="display:flex;gap:.75rem;align-items:flex-end;flex-wrap:wrap;margin-bottom:1rem">
    <label style="margin:0">
      Posts this run
      <input type="number" name="posts_this_run" value="{ppr}" min="1" max="50" step="1" style="width:80px;margin:0">
    </label>
    <button type="submit" style="margin-bottom:1px" {'disabled' if state == 'running' else ''}>
      {'⏳ Running…' if state == 'running' else '▶ Run Now'}
    </button>
  </form>
  <table style="margin-top:.5rem">
    <tr><td style="width:140px">State</td><td><span style="color:{state_color};font-weight:600">{state}</span></td></tr>
    <tr><td>Next scheduled</td><td>{next_run} UTC</td></tr>
  </table>
  {last_post_html}
  {run_stats_html}
</article>
"""
    return layout(body, active="sc", title="Scheduling")


@APP.get("/scheduling")
def scheduling_page():
    return _scheduling_page_html()


@APP.post("/scheduling/save")
def scheduling_save():
    notice = ""
    form = request.form
    payload: dict = {}
    try:
        payload["interval_hours"] = float(form.get("interval_hours", 4))
        payload["posts_per_run"] = int(form.get("posts_per_run", 1))
        payload["max_failures_per_run"] = int(form.get("max_failures_per_run", 10))
        payload["failure_alert_enabled"] = bool(form.get("failure_alert_enabled"))
        payload["failure_alert_webhook"] = form.get("failure_alert_webhook", "").strip()
        r = requests.post(f"{SCHEDULER_URL}/api/config", json=payload, timeout=10)
        if r.ok:
            notice = "<article><p style='color:#4caf50'>✅ Settings saved.</p></article>"
        else:
            notice = f"<article><p style='color:#f44336'>⚠ Scheduler returned {r.status_code}: {r.text[:200]}</p></article>"
    except Exception as exc:
        notice = f"<article><p style='color:#f44336'>⚠ {exc}</p></article>"
    return _scheduling_page_html(notice=notice)


@APP.post("/scheduling/trigger")
def scheduling_trigger():
    notice = ""
    try:
        posts_this_run = int(request.form.get("posts_this_run", 1))
    except (TypeError, ValueError):
        posts_this_run = 1
    try:
        r = requests.post(
            f"{SCHEDULER_URL}/api/trigger",
            json={"posts_this_run": posts_this_run},
            timeout=10,
        )
        if r.ok:
            data = r.json()
            if data.get("status") == "already_running":
                notice = "<article><p style='color:#ff9800'>⚠ Scheduler is already running — try again shortly.</p></article>"
            else:
                notice = f"<article><p style='color:#4caf50'>✅ Triggered ({posts_this_run} post{'s' if posts_this_run != 1 else ''}). Refresh to see result.</p></article>"
        else:
            notice = f"<article><p style='color:#f44336'>⚠ Scheduler returned {r.status_code}: {r.text[:200]}</p></article>"
    except Exception as exc:
        notice = f"<article><p style='color:#f44336'>⚠ {exc}</p></article>"
    return _scheduling_page_html(notice=notice)


# ── JSON API ──────────────────────────────────────────────────────────────────

@APP.get("/api/health")
def api_health():
    return jsonify({"worker": worker_health(), "scheduler": scheduler_health(),
                    "time": datetime.now(timezone.utc).isoformat()})


@APP.get("/api/subreddits")
def api_subs_get():
    return jsonify(load_subs())


@APP.post("/api/subreddits")
def api_subs_post():
    body = request.get_json(silent=True) or {}
    subs = body.get("subreddits")
    if not isinstance(subs, list):
        return jsonify({"error": "expected {subreddits:[...]}"}), 400
    save_subs(subs)
    return jsonify({"ok": True, "count": len(subs)})


if __name__ == "__main__":
    APP.run(host="0.0.0.0", port=8080, debug=False)
