# JackDash API

A small FastAPI JSON wrapper for the publicly accessible JackDash GDPS endpoints. Includes in-memory TTL caching and stale-cache fallback if JackDash temporarily errors.

## Endpoints

- `GET /` — endpoint index
- `GET /api/v1/health` — health check
- `GET /api/v1/levels?sort=recent&page=0` — recent levels
- `GET /api/v1/levels?sort=featured&page=0` — featured category (upstream type 6; verify JackDash's category behavior)
- `GET /api/v1/levels?sort=popular&page=0` — popular category (upstream type 2)
- `GET /api/v1/levels?sort=downloaded&page=0` — most downloaded (upstream type 1)
- `GET /api/v1/levels?sort=trending&page=0` — trending (upstream type 3)
- `GET /api/v1/levels/all?page=0` — paginated recent listing; not a guaranteed complete database export
- `GET /api/v1/levels/search?q=Dash&page=0` — level search (upstream type 0)
- `GET /api/v1/levels/daily` — attempts `getGJDailyLevel.php`; reports unavailable if unsupported
- `GET /api/v1/levels/weekly` — attempts `getGJWeeklyLevel.php`; reports unavailable if unsupported
- `GET /api/v1/lists` — parses the public dashboard lists table HTML
- `GET /api/v1/cache` — local cache status

Interactive API docs: `/docs`.

## Important protocol notes

Confirmed by a manual request on 2026-10-17: `POST https://jackdash.ps.fhgdps.com/getGJLevels21.php` returned a Geometry Dash-style level-list response containing level IDs 748 through 739, with names such as `worse than the worst`, `underwater v3`, `River auto`, `Dash`, and `Deadlocked`. The same host's `/database/getGJLevels21.php` path returned 404. Both `/getGJLevels21.php` and `/getGJLevels.php` returned 200 with the tested payload; this wrapper uses `getGJLevels21.php`.

Daily/weekly endpoints, sort types, and dashboard HTML parsing are best-effort and need to be verified against JackDash itself. A successful HTTP status does not necessarily mean the upstream returned a valid category. The `raw_fields` object preserves source fields so the mapping can be refined.

`/levels/all` deliberately does not claim to export every level in the server database. It returns a page from the upstream recent listing. A true full catalog needs a reliable server-supported listing/search strategy and enough pagination coverage.

## Run locally (Termux, Linux, macOS, Windows)

Python 3.11+ recommended.

```bash
python -m venv .venv
# Linux/macOS/Termux:
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open `http://127.0.0.1:8000/docs`.

## Deploy from GitHub to Render

1. Create a GitHub repository named `jackdash-api`.
2. Upload/push all files in this folder to its root.
3. In Render, choose **New + → Blueprint**, connect the repository, and let Render read `render.yaml`.
4. Deploy. Render will provide a URL such as `https://jackdash-api.onrender.com` (the exact name may differ).
5. Test `/api/v1/health`, then `/api/v1/levels/recent?page=0` and `/api/v1/lists`.

Render free services may sleep when idle, and their local cache is ephemeral and per instance. For durable shared caching, add Redis or a database later. No API key is required by this wrapper by default; consider adding authentication/rate limiting before making it widely public.

## Cache behavior

- recent/all/trending: 120 seconds by default
- other level searches: 180 seconds
- daily/weekly: 300 seconds
- lists page: 600 seconds
- stale data is returned if a refresh fails and a previous cache entry exists

Set the environment variables in `.env.example` to customize these values.
