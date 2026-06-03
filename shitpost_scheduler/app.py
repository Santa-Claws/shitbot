import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

import requests
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, jsonify

APP = Flask(__name__)

WORKER_URL = os.environ.get("SHITPOST_SERVICE_URL", "http://shitpost-worker:8000").rstrip("/")
DISCORD_WEBHOOK_URL = os.environ.get("SHITPOST_DISCORD_WEBHOOK_URL", "")
INTERVAL_HOURS = float(os.environ.get("SHITPOST_INTERVAL_HOURS", "4"))
MEDIA_DIR = Path(os.environ.get("SHITPOST_MEDIA_DIR", "/media"))

_lock = threading.Lock()
_status: dict = {
    "state": "idle",
    "last_run": None,
    "last_result": None,
    "last_error": None,
    "last_post": None,
}


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


def run_post_job() -> None:
    if not _lock.acquire(blocking=False):
        return

    _status["state"] = "running"
    _status["last_run"] = datetime.now(timezone.utc).isoformat()
    result, error, last_post = "skipped", None, None

    try:
        pool_r = requests.post(f"{WORKER_URL}/api/feed-pool", json={"subreddits": []}, timeout=30)
        pool_r.raise_for_status()
        candidates = pool_r.json().get("items", [])

        sel_r = requests.post(
            f"{WORKER_URL}/api/select-attempts",
            json={"queue": candidates, "limit": 10},
            timeout=10,
        )
        sel_r.raise_for_status()
        items = sel_r.json().get("items", [])

        for item in items:
            prep_r = requests.post(f"{WORKER_URL}/api/prepare-attempt", json=item, timeout=130)
            prep_data = prep_r.json()
            attempt_id = prep_data.get("attemptId")
            status = prep_data.get("status")

            if status != "ready":
                # Worker already finalized skip/error cases; just move on
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
                result = "posted"
                last_post = {
                    "title": (item.get("title") or "")[:100],
                    "subreddit": item.get("subreddit", ""),
                }
                break

    except Exception as exc:
        result = "error"
        error = str(exc)[:500]
    finally:
        _status.update({"state": "idle", "last_result": result, "last_error": error, "last_post": last_post})
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
    return jsonify({**_status, "next_run": _next_run()})


@APP.post("/api/trigger")
def api_trigger():
    if _status["state"] == "running":
        return jsonify({"status": "already_running"}), 409
    threading.Thread(target=run_post_job, daemon=True).start()
    return jsonify({"status": "triggered"})


scheduler = BackgroundScheduler(timezone="UTC")
scheduler.add_job(run_post_job, "interval", hours=INTERVAL_HOURS, id="post_job")

if __name__ == "__main__":
    scheduler.start()
    APP.run(host="0.0.0.0", port=8001, debug=False)
