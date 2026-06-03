# n8n Shitpost Bot

Self-hosted n8n instance with a Reddit-to-Discord shitpost bot workflow. Runs on Docker Compose with a sidecar media worker.

## What it does

Every 4 hours, the workflow:
1. Pulls the weekly top posts from 7 subreddits via RSS
2. Shuffles the pool and selects up to 10 posts not already seen
3. Downloads each post's media (video via yt-dlp, images directly)
4. Posts the file to a Discord webhook
5. Records every attempt in a SQLite registry so nothing gets reposted

**Subreddits:** r/MemeVideos, r/MurderedByWords, r/blursed_videos, r/Kitchencels, r/addressme, r/CursedGuns, r/perfectlycutvideos

## Architecture

```
n8n (port 5678)  <-->  shitpost-worker (port 8000, internal)
                              |
                        SQLite registry (/data/)
                        Shared media volume (./shared_media)
```

The `shitpost-worker` sidecar owns all the fragile parts: RSS fetching, `yt-dlp` downloads, file hashing, and the anti-repost SQLite registry. n8n stays thin — it handles scheduling, per-item orchestration, Discord uploads, and cleanup calls.

## Setup

### 1. Copy and fill in secrets

```bash
cp .env.example .env
```

Edit `.env`:
- `N8N_HOST` — your machine's LAN IP
- `WEBHOOK_URL` — same IP with port
- `N8N_BASIC_AUTH_PASSWORD` — set something strong
- `SHITPOST_DISCORD_WEBHOOK_URL` — your Discord webhook URL

### 2. Start

```bash
docker compose up -d
```

First run builds the `shitpost-worker` image (includes `yt-dlp` + `ffmpeg`, takes a few minutes).

### 3. Activate the workflow

Open `http://<your-lan-ip>:5678`, find **Shitpost Bot - Reddit Weekly to Discord**, and toggle it active.

### 4. Stop

```bash
docker compose down
```

## Workflow management

The workflow is tracked as TypeScript in `automations/` using n8n-as-code. See [`docs/n8n-as-code.md`](docs/n8n-as-code.md) for setup and usage.

## File layout

```
.
├── docker-compose.yml
├── .env.example
├── shitpost_worker/
│   ├── app.py          # Flask sidecar: feed fetch, download, registry
│   ├── Dockerfile
│   └── requirements.txt
├── automations/
│   └── workflows/local_5678_mira_c/personal/
│       └── Shitpost Bot - Reddit Weekly to Discord.workflow.ts
├── shared_media/       # ephemeral media files (gitignored)
├── shitpost_data/      # SQLite registry (gitignored)
└── n8n_data/           # n8n database + config (gitignored)
```

## Anti-repost registry

The worker maintains a SQLite database at `shitpost_data/repost_registry.sqlite`. Every attempt — successful or failed — is recorded permanently. A post is blocked if its post ID, URL, or content hash matches anything already in the registry.

## Sidecar API

| Method | Path | Description |
|--------|------|-------------|
| GET | `/healthz` | Health check |
| POST | `/api/feed-pool` | Fetch RSS pool for given subreddits |
| POST | `/api/select-attempts` | Filter queue against registry, return up to N candidates |
| POST | `/api/prepare-attempt` | Create registry entry + download media |
| POST | `/api/finalize-attempt` | Mark outcome, delete temp file |

## Notes

- Max file size: 24 MB (Discord limit)
- Download timeout: 120 seconds per item
- `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` is required for the workflow to read env vars
- Data is on LAN HTTP only — put n8n behind HTTPS before exposing to the internet
