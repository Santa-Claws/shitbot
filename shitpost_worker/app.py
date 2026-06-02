import glob
import hashlib
import html
import json
import mimetypes
import os
import random
import re
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import feedparser
import requests
from flask import Flask, jsonify, request


APP = Flask(__name__)

RSS_USER_AGENT = os.getenv(
    "SHITPOST_RSS_USER_AGENT",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
)
REGISTRY_PATH = Path(os.getenv("SHITPOST_REGISTRY_PATH", "/data/repost_registry.sqlite"))
SHARED_MEDIA_DIR = Path(os.getenv("SHITPOST_SHARED_MEDIA_DIR", "/shared_media"))
DOWNLOAD_TIMEOUT_SECONDS = int(os.getenv("SHITPOST_DOWNLOAD_TIMEOUT_SECONDS", "120"))
MAX_MEDIA_BYTES = int(os.getenv("SHITPOST_MAX_MEDIA_BYTES", "24000000"))
REQUEST_TIMEOUT = (10, 30)
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
VIDEO_HOSTS = {"v.redd.it"}
IMAGE_HOST_SUFFIXES = ("i.redd.it", "preview.redd.it", "external-preview.redd.it")
LINK_PATTERN = re.compile(r'<a href="([^"]+)">\[link\]</a>', re.IGNORECASE)
IMG_PATTERN = re.compile(r'<img src="([^"]+)"', re.IGNORECASE)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(REGISTRY_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    SHARED_MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    with db_connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS repost_registry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subreddit TEXT,
                title TEXT,
                canonical_post_id TEXT,
                original_post_url TEXT,
                canonical_post_url TEXT,
                original_media_url TEXT,
                canonical_media_url TEXT,
                content_hash TEXT,
                media_bytes INTEGER,
                mime_type TEXT,
                shared_path TEXT,
                file_name TEXT,
                source_feed_url TEXT,
                status TEXT NOT NULL,
                failure_reason TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                finalized_at TEXT
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_registry_post_id
            ON repost_registry(canonical_post_id)
            WHERE canonical_post_id IS NOT NULL AND canonical_post_id != '';

            CREATE UNIQUE INDEX IF NOT EXISTS idx_registry_post_url
            ON repost_registry(canonical_post_url)
            WHERE canonical_post_url IS NOT NULL AND canonical_post_url != '';

            CREATE UNIQUE INDEX IF NOT EXISTS idx_registry_media_url
            ON repost_registry(canonical_media_url)
            WHERE canonical_media_url IS NOT NULL AND canonical_media_url != '';

            CREATE UNIQUE INDEX IF NOT EXISTS idx_registry_content_hash
            ON repost_registry(content_hash)
            WHERE content_hash IS NOT NULL AND content_hash != '';
            """
        )


def request_headers() -> dict[str, str]:
    return {"User-Agent": RSS_USER_AGENT}


def json_response(payload: dict[str, Any], status: int = 200):
    return APP.response_class(
        response=json.dumps(payload, ensure_ascii=True),
        status=status,
        mimetype="application/json",
    )


def normalize_text(value: Any) -> str:
    return str(value or "").strip()


def safe_slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "_", value).strip("._")
    return slug or "media"


def canonicalize_url(url: str) -> str:
    return normalize_text(url)


def resolve_url(url: str) -> str:
    url = canonicalize_url(url)
    if not url:
        return ""
    try:
        response = requests.head(
            url,
            allow_redirects=True,
            timeout=REQUEST_TIMEOUT,
            headers=request_headers(),
        )
        if response.ok and response.url:
            return response.url
    except requests.RequestException:
        pass

    try:
        response = requests.get(
            url,
            allow_redirects=True,
            timeout=REQUEST_TIMEOUT,
            headers=request_headers(),
            stream=True,
        )
        response.close()
        if response.url:
            return response.url
    except requests.RequestException:
        return url
    return url


def extract_media_url(content_html: str) -> str:
    decoded = html.unescape(content_html or "")
    link_match = LINK_PATTERN.search(decoded)
    if link_match:
        return link_match.group(1)
    img_match = IMG_PATTERN.search(decoded)
    if img_match:
        return img_match.group(1)
    return ""


def parse_feed(subreddit: str) -> list[dict[str, Any]]:
    feed_url = f"https://www.reddit.com/r/{subreddit}/top/.rss?t=week"
    response = requests.get(
        feed_url,
        headers=request_headers(),
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    parsed = feedparser.parse(response.content)
    results: list[dict[str, Any]] = []
    for entry in parsed.entries:
        content_html = ""
        if entry.get("content"):
            content_html = entry.content[0].value
        media_url = extract_media_url(content_html)
        thumbnail_url = ""
        media_thumbnail = entry.get("media_thumbnail") or []
        if media_thumbnail:
            thumbnail_url = media_thumbnail[0].get("url", "")
        results.append(
            {
                "subreddit": subreddit,
                "title": normalize_text(entry.get("title")),
                "postId": normalize_text(entry.get("id")),
                "permalink": normalize_text(entry.get("link")),
                "mediaUrl": normalize_text(media_url),
                "thumbnailUrl": normalize_text(thumbnail_url),
                "publishedAt": normalize_text(entry.get("published") or entry.get("updated")),
                "feedUrl": feed_url,
            }
        )
    return results


def find_existing_attempt(conn: sqlite3.Connection, candidate: dict[str, Any]) -> sqlite3.Row | None:
    clauses: list[str] = []
    params: list[str] = []

    for column, key in (
        ("canonical_post_id", "postId"),
        ("canonical_post_url", "permalink"),
        ("original_post_url", "permalink"),
        ("canonical_media_url", "mediaUrl"),
        ("original_media_url", "mediaUrl"),
        ("content_hash", "contentHash"),
    ):
        value = normalize_text(candidate.get(key))
        if value:
            clauses.append(f"{column} = ?")
            params.append(value)

    if not clauses:
        return None

    query = (
        "SELECT * FROM repost_registry "
        f"WHERE {' OR '.join(clauses)} "
        "ORDER BY id DESC LIMIT 1"
    )
    return conn.execute(query, params).fetchone()


def choose_media_strategy(media_url: str, permalink: str) -> str:
    parsed = urlparse(media_url or "")
    hostname = (parsed.hostname or "").lower()
    suffix = Path(parsed.path).suffix.lower()

    if hostname in VIDEO_HOSTS:
        return "video"
    if hostname.endswith("reddit.com") and "/gallery/" in parsed.path:
        return "unsupported"
    if hostname.endswith(IMAGE_HOST_SUFFIXES) or suffix in IMAGE_EXTENSIONS:
        return "image"
    if hostname.endswith("reddit.com") and permalink:
        return "video"
    return "unsupported"


def sha256_for_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def cleanup_attempt_files(attempt_id: int) -> list[str]:
    deleted: list[str] = []
    for match in glob.glob(str(SHARED_MEDIA_DIR / f"attempt_{attempt_id}.*")):
        try:
            os.remove(match)
            deleted.append(match)
        except FileNotFoundError:
            continue
    return deleted


def update_attempt(
    conn: sqlite3.Connection,
    attempt_id: int,
    *,
    status: str,
    failure_reason: str | None = None,
    content_hash: str | None = None,
    media_bytes: int | None = None,
    mime_type: str | None = None,
    shared_path: str | None = None,
    file_name: str | None = None,
    finalized: bool = False,
) -> None:
    conn.execute(
        """
        UPDATE repost_registry
        SET status = ?,
            failure_reason = COALESCE(?, failure_reason),
            content_hash = COALESCE(?, content_hash),
            media_bytes = COALESCE(?, media_bytes),
            mime_type = COALESCE(?, mime_type),
            shared_path = ?,
            file_name = ?,
            updated_at = ?,
            finalized_at = CASE WHEN ? THEN ? ELSE finalized_at END
        WHERE id = ?
        """,
        (
            status,
            failure_reason,
            content_hash,
            media_bytes,
            mime_type,
            shared_path,
            file_name,
            now_iso(),
            1 if finalized else 0,
            now_iso() if finalized else None,
            attempt_id,
        ),
    )


def create_attempt(candidate: dict[str, Any]) -> int:
    created_at = now_iso()
    with db_connect() as conn:
        existing = find_existing_attempt(conn, candidate)
        if existing:
            return -int(existing["id"])
        cursor = conn.execute(
            """
            INSERT INTO repost_registry (
                subreddit,
                title,
                canonical_post_id,
                original_post_url,
                canonical_post_url,
                original_media_url,
                canonical_media_url,
                source_feed_url,
                status,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                normalize_text(candidate.get("subreddit")),
                normalize_text(candidate.get("title")),
                normalize_text(candidate.get("postId")),
                normalize_text(candidate.get("permalink")),
                normalize_text(candidate.get("permalink")),
                normalize_text(candidate.get("mediaUrl")),
                normalize_text(candidate.get("mediaUrl")),
                normalize_text(candidate.get("feedUrl")),
                "attempt_started",
                created_at,
                created_at,
            ),
        )
        return int(cursor.lastrowid)


def download_image(url: str, attempt_id: int) -> Path:
    response = requests.get(
        url,
        headers=request_headers(),
        timeout=REQUEST_TIMEOUT,
        stream=True,
    )
    response.raise_for_status()
    content_type = response.headers.get("content-type", "").split(";")[0].strip()
    ext = mimetypes.guess_extension(content_type) or Path(urlparse(url).path).suffix or ".bin"
    path = SHARED_MEDIA_DIR / f"attempt_{attempt_id}{ext}"
    total_bytes = 0
    with path.open("wb") as handle:
        for chunk in response.iter_content(chunk_size=1024 * 256):
            if not chunk:
                continue
            total_bytes += len(chunk)
            if total_bytes > MAX_MEDIA_BYTES:
                handle.close()
                path.unlink(missing_ok=True)
                raise ValueError("image_too_large")
            handle.write(chunk)
    return path


def run_yt_dlp(url: str, attempt_id: int) -> Path:
    output_template = str(SHARED_MEDIA_DIR / f"attempt_{attempt_id}.%(ext)s")
    command = [
        "yt-dlp",
        "--no-progress",
        "--no-warnings",
        "--no-playlist",
        "--restrict-filenames",
        "--socket-timeout",
        "30",
        "--max-filesize",
        f"{MAX_MEDIA_BYTES}",
        "--merge-output-format",
        "mp4",
        "--format",
        "bv*+ba/b/best",
        "--add-header",
        f"User-Agent:{RSS_USER_AGENT}",
        "--output",
        output_template,
        url,
    ]
    subprocess.run(
        command,
        check=True,
        timeout=DOWNLOAD_TIMEOUT_SECONDS,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    matches = sorted(glob.glob(str(SHARED_MEDIA_DIR / f"attempt_{attempt_id}.*")))
    if not matches:
        raise FileNotFoundError("yt_dlp_did_not_create_output")
    return Path(matches[0])


def download_media(candidate: dict[str, Any], attempt_id: int) -> Path:
    media_url = normalize_text(candidate.get("canonicalMediaUrl") or candidate.get("mediaUrl"))
    permalink = normalize_text(candidate.get("canonicalPostUrl") or candidate.get("permalink"))
    strategy = choose_media_strategy(media_url, permalink)

    if strategy == "image":
        return download_image(media_url, attempt_id)

    if strategy == "video":
        errors: list[str] = []
        for url in [media_url, permalink]:
            if not url:
                continue
            try:
                return run_yt_dlp(url, attempt_id)
            except subprocess.TimeoutExpired:
                cleanup_attempt_files(attempt_id)
                raise TimeoutError("download_timeout")
            except Exception as exc:  # noqa: BLE001
                cleanup_attempt_files(attempt_id)
                errors.append(str(exc))
        raise RuntimeError("; ".join(errors) or "video_download_failed")

    raise ValueError("unsupported_media")


@APP.get("/healthz")
def healthz():
    return jsonify({"status": "ok", "time": now_iso()})


SUBREDDITS_CONFIG_PATH = REGISTRY_PATH.parent / "subreddits.json"

DEFAULT_SUBREDDITS = [
    {"name": "MemeVideos", "weight": 1.0},
    {"name": "MurderedByWords", "weight": 1.0},
    {"name": "blursed_videos", "weight": 1.0},
    {"name": "Kitchencels", "weight": 1.0},
    {"name": "addressme", "weight": 1.0},
    {"name": "CursedGuns", "weight": 1.0},
    {"name": "perfectlycutvideos", "weight": 1.0},
]


def load_subreddits() -> list[dict[str, Any]]:
    if SUBREDDITS_CONFIG_PATH.exists():
        try:
            return json.loads(SUBREDDITS_CONFIG_PATH.read_text())
        except Exception:  # noqa: BLE001
            pass
    return DEFAULT_SUBREDDITS


@APP.post("/api/feed-pool")
def feed_pool():
    payload = request.get_json(silent=True) or {}
    requested = payload.get("subreddits") or []
    items: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    if requested:
        sub_configs = [{"name": str(s), "weight": 1.0} for s in requested]
    else:
        sub_configs = load_subreddits()

    weight_map: dict[str, float] = {
        c["name"]: float(c.get("weight") or 1.0) for c in sub_configs
    }

    for config in sub_configs:
        subreddit = config["name"]
        try:
            items.extend(parse_feed(subreddit))
        except Exception as exc:  # noqa: BLE001
            errors.append({"subreddit": subreddit, "error": str(exc)})

    # Weighted shuffle: higher-weight subreddits surface earlier in the queue.
    # Each item draws a uniform random key scaled by 1/weight (Efraimidis-Spirakis).
    for item in items:
        w = weight_map.get(item.get("subreddit", ""), 1.0)
        item["_priority"] = random.random() ** (1.0 / max(w, 0.01))
    items.sort(key=lambda x: x.pop("_priority"), reverse=True)

    return json_response(
        {
            "items": items,
            "errors": errors,
            "fetchedAt": now_iso(),
        }
    )


@APP.post("/api/select-attempts")
def select_attempts():
    payload = request.get_json(silent=True) or {}
    queue = payload.get("queue") or []
    limit = int(payload.get("limit") or 10)
    selected: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    with db_connect() as conn:
        for candidate in queue:
            if len(selected) >= limit:
                break
            key_parts = [
                normalize_text(candidate.get("postId")),
                normalize_text(candidate.get("permalink")),
                normalize_text(candidate.get("mediaUrl")),
            ]
            dedupe_key = "||".join(key_parts)
            if dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)
            if find_existing_attempt(conn, candidate):
                continue
            selected.append(candidate)

    return json_response({"items": selected, "selectedCount": len(selected)})


@APP.post("/api/prepare-attempt")
def prepare_attempt():
    candidate = request.get_json(silent=True) or {}
    candidate["permalink"] = canonicalize_url(candidate.get("permalink"))
    candidate["mediaUrl"] = canonicalize_url(candidate.get("mediaUrl"))
    candidate["canonicalPostUrl"] = resolve_url(candidate["permalink"]) if candidate["permalink"] else ""
    candidate["canonicalMediaUrl"] = resolve_url(candidate["mediaUrl"]) if candidate["mediaUrl"] else ""

    attempt_id = create_attempt(candidate)
    if attempt_id < 0:
        with db_connect() as conn:
            existing = conn.execute(
                "SELECT id, status FROM repost_registry WHERE id = ?",
                (-attempt_id,),
            ).fetchone()
        return json_response(
            {
                "status": "skip",
                "reason": "already_known",
                "countsAsAttempt": False,
                "existingAttemptId": -attempt_id,
                "existingStatus": existing["status"] if existing else "unknown",
            }
        )

    try:
        with db_connect() as conn:
            conn.execute(
                """
                UPDATE repost_registry
                SET canonical_post_url = ?, canonical_media_url = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    candidate["canonicalPostUrl"],
                    candidate["canonicalMediaUrl"],
                    now_iso(),
                    attempt_id,
                ),
            )

        path = download_media(candidate, attempt_id)
        if path.stat().st_size > MAX_MEDIA_BYTES:
            raise ValueError("media_too_large")

        content_hash = sha256_for_file(path)
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"

        with db_connect() as conn:
            dupe = conn.execute(
                """
                SELECT id FROM repost_registry
                WHERE content_hash = ? AND id != ?
                LIMIT 1
                """,
                (content_hash, attempt_id),
            ).fetchone()
            if dupe:
                path.unlink(missing_ok=True)
                update_attempt(
                    conn,
                    attempt_id,
                    status="duplicate_hash",
                    failure_reason=f"matches_attempt_{dupe['id']}",
                    content_hash=content_hash,
                    finalized=True,
                )
                return json_response(
                    {
                        "status": "skip",
                        "reason": "duplicate_hash",
                        "countsAsAttempt": True,
                        "attemptId": attempt_id,
                    }
                )

            update_attempt(
                conn,
                attempt_id,
                status="downloaded",
                content_hash=content_hash,
                media_bytes=path.stat().st_size,
                mime_type=mime_type,
                shared_path=str(path),
                file_name=path.name,
            )

        return json_response(
            {
                "status": "ready",
                "countsAsAttempt": True,
                "attemptId": attempt_id,
                "sharedPath": str(path),
                "fileName": path.name,
                "mimeType": mime_type,
                "mediaBytes": path.stat().st_size,
                "contentHash": content_hash,
                "canonicalPostUrl": candidate["canonicalPostUrl"],
                "canonicalMediaUrl": candidate["canonicalMediaUrl"],
            }
        )
    except TimeoutError as exc:
        cleanup_attempt_files(attempt_id)
        with db_connect() as conn:
            update_attempt(
                conn,
                attempt_id,
                status="download_timeout",
                failure_reason=str(exc),
                finalized=True,
            )
        return json_response(
            {
                "status": "skip",
                "reason": "download_timeout",
                "countsAsAttempt": True,
                "attemptId": attempt_id,
            }
        )
    except ValueError as exc:
        cleanup_attempt_files(attempt_id)
        with db_connect() as conn:
            update_attempt(
                conn,
                attempt_id,
                status=str(exc),
                failure_reason=str(exc),
                finalized=True,
            )
        return json_response(
            {
                "status": "skip",
                "reason": str(exc),
                "countsAsAttempt": True,
                "attemptId": attempt_id,
            }
        )
    except Exception as exc:  # noqa: BLE001
        cleanup_attempt_files(attempt_id)
        with db_connect() as conn:
            update_attempt(
                conn,
                attempt_id,
                status="download_failed",
                failure_reason=str(exc),
                finalized=True,
            )
        return json_response(
            {
                "status": "skip",
                "reason": "download_failed",
                "countsAsAttempt": True,
                "attemptId": attempt_id,
            }
        )


@APP.post("/api/finalize-attempt")
def finalize_attempt():
    payload = request.get_json(silent=True) or {}
    attempt_id = int(payload.get("attemptId") or 0)
    outcome = normalize_text(payload.get("outcome")) or "finalized"
    note = normalize_text(payload.get("note"))
    deleted = cleanup_attempt_files(attempt_id)

    with db_connect() as conn:
        update_attempt(
            conn,
            attempt_id,
            status=outcome,
            failure_reason=note or None,
            shared_path=None,
            file_name=None,
            finalized=True,
        )

    return json_response(
        {
            "status": "ok",
            "attemptId": attempt_id,
            "outcome": outcome,
            "deletedFiles": deleted,
        }
    )


if __name__ == "__main__":
    init_db()
    APP.run(host="0.0.0.0", port=8000)
