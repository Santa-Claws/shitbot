# How it works

## Container architecture

```
┌─────────────────────────────────────────────────────┐
│ Docker host                                         │
│                                                     │
│  ┌──────────────────┐     ┌──────────────────────┐  │
│  │ shitpost-worker  │     │  shitpost-scheduler  │  │
│  │   :8000 (int)    │◄────│     :8001 (int)      │  │
│  └────────┬─────────┘     └──────────────────────┘  │
│           │                         │               │
│    /data/repost_registry.sqlite     │ Discord        │
│    /data/subreddits.json            │ webhook POST   │
│    ./shared_media/                  ▼               │
│                             ┌──────────────────────┐ │
│                             │   shitbot-manager    │ │
│                             │    :8080 (ext)       │ │
│                             └──────────────────────┘ │
└─────────────────────────────────────────────────────┘
```

Each container has a single responsibility. The worker owns all data. The scheduler owns the timing and Discord posting. The manager is read-mostly — it reads the SQLite registry directly and calls the scheduler for status and triggers.

---

## The posting flow

Every `SHITPOST_INTERVAL_HOURS` hours (default 4), the scheduler runs this sequence:

### Step 1 — Fetch the feed pool

`POST /api/feed-pool` on the worker.

The worker fetches the RSS feed for each configured subreddit (`https://www.reddit.com/r/{name}/top/.rss?t=week`). It parses each feed entry and extracts:

- Post ID, title, permalink
- Media URL (direct image, or v.redd.it video link)
- Subreddit name
- Published timestamp

All items from all subreddits are merged into a single flat list, then **weighted-shuffled** using the Efraimidis-Spirakis algorithm: each item gets a sort key of `random() ** (1 / weight)`, then the list is sorted descending. Items from heavier-weighted subreddits naturally float to the top, but there's still randomness — a weight-2.0 subreddit doesn't just dominate every run.

### Step 2 — Deduplicate against the registry

`POST /api/select-attempts` on the worker, passing the full pool.

The worker checks every item's post ID and URL against the `repost_registry` SQLite table. Anything that's already been attempted (regardless of outcome) is filtered out. Returns up to 10 survivors in weighted-shuffle order.

### Step 3 — Try candidates in order

For each candidate from step 2, the scheduler calls `POST /api/prepare-attempt`.

The worker:
1. Creates a new row in `repost_registry` with `status = attempt_started`
2. Resolves any redirects in the permalink and media URL
3. Downloads the media:
   - **Images** — direct HTTP fetch with a browser User-Agent
   - **Videos** (`v.redd.it`) — `yt-dlp` merges the video and audio streams via ffmpeg
4. Rejects if the file exceeds `SHITPOST_MAX_MEDIA_BYTES` (default 24 MB)
5. SHA-256 hashes the downloaded file and checks it against all existing `content_hash` values in the registry — catches cross-posts of the same media from different threads
6. Moves the file to `shared_media/` and updates the registry row to `status = downloaded`
7. Returns `status: "ready"` with `fileName`, `mimeType`, `attemptId`

If any step fails (timeout, unsupported format, hash collision, file too large), the worker finalizes the row itself with the appropriate failure status and returns `status: "skip"`. The scheduler moves to the next candidate.

### Step 4 — Post to Discord

Once a candidate comes back `ready`, the scheduler POSTs to the Discord webhook.

If the file exists in the shared media volume, it's sent as a multipart upload (the actual file bytes). If the file isn't accessible for any reason, it falls back to a JSON-only payload. Either way, no title or link is included — just the media.

### Step 5 — Finalize

`POST /api/finalize-attempt` with outcome `posted` or `discord_failed`.

The worker updates the registry row, records the final status and timestamp, and deletes the media file from `shared_media/`. The registry entry remains permanently so the post is never repeated.

The scheduler stops after the first successful post per run. If all 10 candidates fail (all downloads break, all are duplicates, etc.), the run is logged as `skipped`.

---

## Anti-repost registry

The registry is a SQLite database at `shitpost_data/repost_registry.sqlite`. Every attempt ever made is recorded permanently.

Dedup happens at two independent points:

1. **Before download** (`select-attempts`) — filters by `canonical_post_id` and `canonical_post_url`. A post from the same Reddit thread is never retried even if it failed before.

2. **After download** (`prepare-attempt`) — compares `content_hash` (SHA-256 of the file bytes) against all existing entries. The same image or video posted to multiple subreddits will only ever go out once.

Key columns in `repost_registry`:

| Column | Description |
|---|---|
| `canonical_post_id` | Reddit post ID extracted from the permalink |
| `canonical_post_url` | Resolved (redirect-followed) permalink |
| `canonical_media_url` | Resolved media URL |
| `content_hash` | SHA-256 of the downloaded file |
| `status` | `attempt_started`, `downloaded`, `posted`, `duplicate_hash`, `download_failed`, `download_timeout`, `unsupported_media`, `media_too_large`, `discord_failed` |
| `finalized_at` | Timestamp of final outcome |
| `failure_reason` | Human-readable error string for failed attempts |

---

## Subreddit weighting

Weights are stored in `shitpost_data/subreddits.json`:

```json
[
  {"name": "MemeVideos", "weight": 1.0},
  {"name": "Kitchencels", "weight": 2.0}
]
```

The weighted shuffle key for each item is:

```python
priority = random.random() ** (1.0 / weight)
```

Higher weight → higher expected priority. At weight 1.0, the key is just `random()`. At weight 2.0, the key is `random() ** 0.5` — always ≥ the weight-1.0 key on average, so heavier subreddits float higher. Weights below 1.0 suppress a subreddit (rarely selected even if it has content).

---

## Scheduler internals

The scheduler is a Flask app + APScheduler in the same process. APScheduler runs `run_post_job()` on an interval trigger. Flask exposes two endpoints the manager uses:

| Endpoint | Description |
|---|---|
| `GET /api/status` | Returns current state (`idle`/`running`), last run time, last result, next scheduled run, last post title/subreddit |
| `POST /api/trigger` | Runs the job immediately in a background thread; 409 if already running |

A threading lock prevents overlapping runs if a manual trigger fires while a scheduled run is in progress.

---

## Worker API reference

| Method | Path | Input | Output |
|---|---|---|---|
| `GET` | `/healthz` | — | `{"status": "ok"}` |
| `POST` | `/api/feed-pool` | `{"subreddits": [...]}` (empty = use subreddits.json) | `{"items": [...], "errors": [...]}` |
| `POST` | `/api/select-attempts` | `{"queue": [...], "limit": N}` | `{"items": [...]}` |
| `POST` | `/api/prepare-attempt` | candidate object from feed-pool | `{"status": "ready"|"skip", "attemptId": N, "fileName": "..."}` |
| `POST` | `/api/finalize-attempt` | `{"attemptId": N, "outcome": "posted"|...}` | `{"status": "ok"}` |

---

## Data volumes

| Path on host | Mounted in | Purpose |
|---|---|---|
| `./shitpost_data` | worker `/data`, manager `/data` | SQLite registry + subreddits.json |
| `./shared_media` | worker (rw), scheduler (ro), manager (ro) | Temporary downloaded files |
