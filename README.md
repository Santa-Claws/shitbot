# shitbot

Pulls the weekly top posts from a configurable list of subreddits and drops them into a Discord channel every 4 hours. Images and videos only, no text, no links. Built to run self-hosted on a Proxmox LXC or any Docker host.

## How it works (short version)

Three containers talk to each other:

- **shitpost-worker** — fetches Reddit RSS, downloads media, tracks what's been posted
- **shitpost-scheduler** — runs every 4 hours, picks a post, calls the worker, sends it to Discord
- **shitbot-manager** — web UI on port 8080 for managing subreddits and watching what's happening

See [`docs/how-it-works.md`](docs/how-it-works.md) for the full breakdown.

---

## Setup

### Prerequisites

- Docker + Docker Compose
- A Discord webhook URL

### 1. Clone

```bash
git clone https://github.com/Santa-Claws/shitbot
cd shitbot
```

### 2. Configure

```bash
cp .env.example .env
```

Edit `.env` — the only required fields:

| Variable | What to set |
|---|---|
| `SHITPOST_DISCORD_WEBHOOK_URL` | Your Discord webhook URL |
| `TZ` | Your timezone, e.g. `America/New_York` |

Everything else has sane defaults. Optional tweaks:

| Variable | Default | Description |
|---|---|---|
| `SHITPOST_INTERVAL_HOURS` | `4` | How often to post |
| `SHITBOT_MANAGER_PORT` | `8080` | Port for the management UI |
| `SHITPOST_MAX_MEDIA_BYTES` | `24000000` | Max file size (Discord limit is 25MB) |
| `SHITPOST_DOWNLOAD_TIMEOUT_SECONDS` | `120` | Per-item download timeout |

### 3. Start

```bash
docker compose up -d
```

First run builds the worker image (pulls yt-dlp + ffmpeg — takes a minute or two). After that, the scheduler fires immediately and then every `SHITPOST_INTERVAL_HOURS` hours.

### 4. Open the management UI

```
http://<your-host-ip>:8080
```

From here you can add/remove subreddits, adjust weights, view post history, and trigger a run manually.

---

## Managing subreddits

Go to **http://\<host\>:8080/subreddits**.

- **Add** a subreddit by name + weight
- **Remove** any subreddit
- **Set weight** to control how often a subreddit is selected relative to others — weight 2.0 means roughly twice as likely to be picked as weight 1.0

Changes take effect on the next run. The config lives at `shitpost_data/subreddits.json` and can also be edited directly.

Default subreddits: r/MemeVideos, r/MurderedByWords, r/blursed_videos, r/Kitchencels, r/addressme, r/CursedGuns, r/perfectlycutvideos

---

## Management UI pages

| Page | What it does |
|---|---|
| `/` | Dashboard — stats, recent posts, system health |
| `/subreddits` | Add, remove, and reweight subreddits |
| `/history` | Full post history with filters |
| `/errors` | Error log grouped by failure type |
| `/stats` | Charts — posts per day, per subreddit, media type breakdown |
| `/feed-preview` | Live preview of what's currently in the RSS pool |
| `/trigger` | Manual "Run Now" button + scheduler status |

---

## File layout

```
shitbot/
├── docker-compose.yml
├── .env.example
├── shitpost_worker/        # data layer container
│   ├── app.py
│   ├── Dockerfile
│   └── requirements.txt
├── shitpost_scheduler/     # orchestration + cron container
│   ├── app.py
│   ├── Dockerfile
│   └── requirements.txt
├── shitbot_manager/        # web UI container
│   ├── app.py
│   ├── Dockerfile
│   └── requirements.txt
├── docs/
│   └── how-it-works.md
├── shitpost_data/          # SQLite registry + subreddits.json (gitignored)
└── shared_media/           # ephemeral downloaded files (gitignored)
```

---

## Branches

| Branch | Description |
|---|---|
| `main` | Current — lightweight Python scheduler, no n8n |
| `n8n` | Legacy — same bot orchestrated through n8n |
