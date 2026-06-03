import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

import requests
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, jsonify, request

APP = Flask(__name__)

WORKER_URL = os.environ.get("SHITPOST_SERVICE_URL", "http://shitpost-worker:8000").rstrip("/")
DISCORD_WEBHOOK_URL = os.environ.get("SHITPOST_DISCORD_WEBHOOK_URL", "")
INTERVAL_HOURS = float(os.environ.get("SHITPOST_INTERVAL_HOURS", "4"))
MEDIA_DIR = Path(os.environ.get("SHITPOST_MEDIA_DIR", "/media"))
SCHEDULING_CONFIG_PATH = Path(os.environ.get("SHITPOST_DATA_DIR", "/data")) / "scheduling.json"

DEFAULT_CONFIG: dict = {
    "interval_hours": INTERVAL_HOURS,
    "posts_per_run": 1,
    "max_failures_per_run": 10,
    "failure_alert_enabled": True,
    "failure_alert_webhook": "",
}

_lock = threading.Lock()
_status: dict = {
    "state": "idle",
    "last_run": None,
    "last_result": None,
    "last_error": None,
    "last_post": None,
    "last_run_stats": {"posted": 0, "failures": 0},
}


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if SCHEDULING_CONFIG_PATH.exists():
        try:
            saved = json.loads(SCHEDULING_CONFIG_PATH.read_text())
            cfg.update({k: v for k, v in saved.items() if k in DEFAULT_CONFIG})
        except Exception:
            pass
    return cfg


def save_config(cfg: dict) -> None:
    SCHEDULING_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCHEDULING_CONFIG_PATH.write_text(json.dumps(cfg, indent=2))


def post_to_discord(item: dict, file_name: str | None, mime_type: str | None) -> bool:
    payload = {"username": "shitbot"}

    if file_name:
        file_path = MEDIA_DIR / file_name
        if file_path.exists():
            try:
                with open(file_path, "rb") as fh:
                    resp = requests.post(
                        DISCORD_WEBHOOK_URL,
                        data={"payload_json": json.dumps(payload)},
                        files={"file": (file_name, fh, mime_type or "application/octet-stream")},
                        timeout=60,
                    )
                return resp.status_code in (200, 204)
            except Exception:
                pass

    try:
        resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=15)
        return resp.status_code in (200, 204)
    except Exception:
        return False


def send_failure_alert(cfg: dict, failure_count: int) -> None:
    if not cfg.get("failure_alert_enabled"):
        return
    webhook = cfg.get("failure_alert_webhook", "").strip()
    if not webhook:
        return
    msg = f"⚠️ shitbot hit {failure_count} failures this run and stopped early. Check the error log."
    try:
        requests.post(webhook, json={"content": msg, "username": "shitbot"}, timeout=15)
    except Exception:
        pass


def run_post_job(posts_this_run: int | None = None) -> None:
    if not _lock.acquire(blocking=False):
        return

    cfg = load_config()
    target = posts_this_run if posts_this_run is not None else cfg["posts_per_run"]
    max_fail = cfg["max_failures_per_run"]
    select_limit = target + max_fail

    _status["state"] = "running"
    _status["last_run"] = datetime.now(timezone.utc).isoformat()
    result, error, last_post = "skipped", None, None
    posted_count, failure_count = 0, 0

    try:
        pool_r = requests.post(f"{WORKER_URL}/api/feed-pool", json={"subreddits": []}, timeout=30)
        pool_r.raise_for_status()
        candidates = pool_r.json().get("items", [])

        sel_r = requests.post(
            f"{WORKER_URL}/api/select-attempts",
            json={"queue": candidates, "limit": select_limit},
            timeout=10,
        )
        sel_r.raise_for_status()
        items = sel_r.json().get("items", [])

        for item in items:
            if posted_count >= target:
                break
            if failure_count >= max_fail:
                send_failure_alert(cfg, failure_count)
                result = "failure_limit_reached"
                break

            prep_r = requests.post(f"{WORKER_URL}/api/prepare-attempt", json=item, timeout=130)
            prep_data = prep_r.json()
            attempt_id = prep_data.get("attemptId")
            status = prep_data.get("status")

            if status != "ready":
                failure_count += 1
                continue

            file_name = prep_data.get("fileName")
            mime_type = prep_data.get("mimeType")
            ok = post_to_discord(item, file_name, mime_type)
            outcome = "posted" if ok else "discord_failed"

            requests.post(
                f"{WORKER_URL}/api/finalize-attempt",
                json={"attemptId": attempt_id, "outcome": outcome},
                timeout=10,
            )

            if ok:
                posted_count += 1
                last_post = {
                    "title": (item.get("title") or "")[:100],
                    "subreddit": item.get("subreddit", ""),
                }
            else:
                failure_count += 1

        if posted_count > 0 and result != "failure_limit_reached":
            result = "posted"

    except Exception as exc:
        result = "error"
        error = str(exc)[:500]
    finally:
        _status.update({
            "state": "idle",
            "last_result": result,
            "last_error": error,
            "last_post": last_post,
            "last_run_stats": {"posted": posted_count, "failures": failure_count},
        })
        _lock.release()


def _next_run() -> str | None:
    jobs = scheduler.get_jobs()
    if jobs and jobs[0].next_run_time:
        return jobs[0].next_run_time.isoformat()
    return None


@APP.get("/healthz")
def healthz():
    return jsonify({"status": "ok"})


@APP.get("/api/status")
def api_status():
    return jsonify({**_status, "next_run": _next_run(), "config": load_config()})


@APP.post("/api/trigger")
def api_trigger():
    if _status["state"] == "running":
        return jsonify({"status": "already_running"}), 409
    body = request.get_json(silent=True) or {}
    posts_this_run = body.get("posts_this_run")
    if posts_this_run is not None:
        try:
            posts_this_run = max(1, int(posts_this_run))
        except (TypeError, ValueError):
            posts_this_run = None
    threading.Thread(target=run_post_job, args=(posts_this_run,), daemon=True).start()
    return jsonify({"status": "triggered"})


@APP.get("/api/config")
def api_config_get():
    return jsonify(load_config())


@APP.post("/api/config")
def api_config_post():
    body = request.get_json(silent=True) or {}
    cfg = load_config()
    old_interval = cfg["interval_hours"]

    for key in DEFAULT_CONFIG:
        if key in body:
            val = body[key]
            if key == "interval_hours":
                val = max(0.1, float(val))
            elif key == "posts_per_run":
                val = max(1, int(val))
            elif key == "max_failures_per_run":
                val = max(1, int(val))
            elif key == "failure_alert_enabled":
                val = bool(val)
            elif key == "failure_alert_webhook":
                val = str(val).strip()
            cfg[key] = val

    save_config(cfg)

    new_interval = cfg["interval_hours"]
    if new_interval != old_interval:
        job = scheduler.get_job("post_job")
        if job:
            scheduler.reschedule_job("post_job", trigger="interval", hours=new_interval)

    return jsonify({"status": "ok", "config": cfg})


scheduler = BackgroundScheduler(timezone="UTC")
scheduler.add_job(run_post_job, "interval", hours=INTERVAL_HOURS, id="post_job")

if __name__ == "__main__":
    scheduler.start()
    APP.run(host="0.0.0.0", port=8001, debug=False)
