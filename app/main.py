from __future__ import annotations

import asyncio
import os
import time
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

APP_NAME = "JackDash API"
BASE_URL = os.getenv("JACKDASH_BASE_URL", "https://jackdash.ps.fhgdps.com").rstrip("/")
GDPS_SECRET = os.getenv("JACKDASH_SECRET", "Wmfd2893gb7")
GAME_VERSION = os.getenv("GD_GAME_VERSION", "22")
BINARY_VERSION = os.getenv("GD_BINARY_VERSION", "47")
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "15"))
DEFAULT_TTL = int(os.getenv("CACHE_TTL_SECONDS", "180"))
RECENT_TTL = int(os.getenv("RECENT_CACHE_TTL_SECONDS", "120"))
SPECIAL_TTL = int(os.getenv("SPECIAL_CACHE_TTL_SECONDS", "300"))
LISTS_TTL = int(os.getenv("LISTS_CACHE_TTL_SECONDS", "600"))
MAX_PAGES = int(os.getenv("MAX_PAGES_PER_REQUEST", "10"))

app = FastAPI(
    title=APP_NAME,
    description=(
        "A cached JSON wrapper around publicly accessible JackDash / Geometry Dash "
        "private-server endpoints. Daily/weekly/list support is reported honestly when unavailable."
    ),
    version="1.0.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)

# In-process TTL cache. On Render's single instance this avoids repeated upstream calls.
_cache: dict[str, tuple[float, Any]] = {}
_locks: dict[str, asyncio.Lock] = {}
_upstream_semaphore = asyncio.Semaphore(3)


def cache_get(key: str) -> tuple[bool, Any, bool]:
    entry = _cache.get(key)
    if not entry:
        return False, None, False
    expires, value = entry
    return time.time() < expires, value, True


def cache_put(key: str, value: Any, ttl: int) -> None:
    _cache[key] = (time.time() + ttl, value)


async def cached(key: str, ttl: int, loader):
    fresh, value, exists = cache_get(key)
    if fresh:
        return value, "HIT"
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        fresh, value, exists = cache_get(key)
        if fresh:
            return value, "HIT"
        try:
            result = await loader()
            cache_put(key, result, ttl)
            return result, "MISS"
        except Exception:
            # Serve the last value even after expiry if upstream fails.
            if exists:
                return value, "STALE"
            raise


async def post_gd(endpoint: str, data: dict[str, str]) -> str:
    payload = {
        "secret": GDPS_SECRET,
        "gameVersion": GAME_VERSION,
        "binaryVersion": BINARY_VERSION,
        **data,
    }
    async with _upstream_semaphore:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
            response = await client.post(f"{BASE_URL}/{endpoint}", data=payload)
            response.raise_for_status()
            return response.text.strip()


def parse_level_record(record: str) -> dict[str, Any] | None:
    """Parse a Geometry Dash key:value level record while preserving all raw fields."""
    if not record or not record.startswith("1:"):
        return None
    parts = record.split(":")
    fields: dict[str, str] = {}
    # GDPS records are alternating numeric field id and field value pairs.
    for i in range(0, len(parts) - 1, 2):
        key = parts[i]
        if key.isdigit():
            fields[key] = parts[i + 1]
    if "1" not in fields:
        return None

    def integer(key: str):
        try:
            return int(fields[key])
        except (KeyError, ValueError):
            return None

    # Standard GD level response field identifiers. Keep raw_fields so consumers
    # can reinterpret server-specific/custom fields without losing information.
    return {
        "id": fields.get("1"),
        "name": fields.get("2", ""),
        "description_encoded": fields.get("3", ""),
        "creator_id": fields.get("5"),
        "difficulty_raw": integer("6"),
        "downloads": integer("8"),
        "audio_track": integer("9"),
        "game_version": integer("10"),
        "likes": integer("12"),
        "length": integer("13"),
        "demon": integer("17"),
        "stars": integer("18"),
        "featured": integer("19"),
        "object_count": integer("45"),
        "song_id": integer("35"),
        "raw_fields": fields,
    }


def parse_level_list(response: str) -> dict[str, Any]:
    # Geometry Dash replies use # between the level section and extra metadata.
    level_section = response.split("#", 1)[0]
    levels = [item for part in level_section.split("|") if (item := parse_level_record(part))]
    return {"levels": levels, "count": len(levels), "raw_prefix": response[:80] if not levels else None}


def parse_special(response: str, kind: str) -> dict[str, Any]:
    if not response or response == "-1":
        return {"available": False, "kind": kind, "message": "JackDash returned no special-level data", "raw": response}
    # Daily/weekly endpoints typically return a compact colon-delimited record.
    parts = response.split(":")
    return {"available": True, "kind": kind, "raw": response, "parts": parts}


async def level_search(*, level_type: int, page: int = 0, query: str = "", extra: dict[str, str] | None = None):
    params = {"type": str(level_type), "page": str(page), "str": query}
    if extra:
        params.update(extra)
    response = await post_gd("getGJLevels21.php", params)
    parsed = parse_level_list(response)
    parsed.update({"page": page, "type": level_type, "query": query, "upstream": "getGJLevels21.php"})
    return parsed


async def fetch_lists() -> dict[str, Any]:
    url = f"{BASE_URL}/dashboard/stats/listsTable.php"
    async with _upstream_semaphore:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    tables = []
    for table in soup.find_all("table"):
        rows = []
        for tr in table.find_all("tr"):
            cells = tr.find_all(["th", "td"])
            if not cells:
                continue
            row = []
            for cell in cells:
                links = [{"text": a.get_text(" ", strip=True), "url": urljoin(url, a.get("href", ""))}
                         for a in cell.find_all("a", href=True)]
                row.append({"text": cell.get_text(" ", strip=True), "links": links})
            rows.append(row)
        if rows:
            tables.append(rows)
    return {
        "source_url": url,
        "available": bool(tables),
        "tables": tables,
        "message": None if tables else "No HTML tables found; the page may require JavaScript or a different route.",
    }


@app.get("/")
async def root():
    return {
        "name": APP_NAME,
        "version": app.version,
        "docs": "/docs",
        "health": "/api/v1/health",
        "endpoints": [
            "/api/v1/levels?sort=recent&page=0",
            "/api/v1/levels?sort=featured&page=0",
            "/api/v1/levels?sort=popular&page=0",
            "/api/v1/levels/recent",
            "/api/v1/levels/featured",
            "/api/v1/levels/search?q=Dash",
            "/api/v1/levels/all?page=0",
            "/api/v1/levels/daily",
            "/api/v1/levels/weekly",
            "/api/v1/lists",
        ],
        "note": "All-levels means a paginated catalog view; a complete database export is not guaranteed by the upstream API.",
    }


@app.get("/api/v1/health")
async def health():
    return {"ok": True, "upstream": BASE_URL, "cache_entries": len(_cache)}


@app.get("/api/v1/levels")
async def levels(
    sort: str = Query("recent", pattern="^(recent|featured|popular|downloaded|trending|all)$"),
    page: int = Query(0, ge=0, le=100000),
    q: str = Query("", max_length=100),
):
    type_map = {"recent": 4, "featured": 6, "popular": 2, "downloaded": 1, "trending": 3, "all": 4}
    # JackDash has confirmed type=4 recent results. 'all' exposes the paginated
    # catalog reachable through that listing; it is not a claim of a full DB dump.
    level_type = type_map[sort]
    if q:
        level_type = 0
    key = f"levels:{sort}:{level_type}:{page}:{q.strip().lower()}"
    ttl = RECENT_TTL if sort in ("recent", "all", "trending") else DEFAULT_TTL
    async def loader():
        return await level_search(level_type=level_type, page=page, query=q)
    try:
        data, cache_status = await cached(key, ttl, loader)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(502, detail=f"JackDash upstream returned HTTP {exc.response.status_code}")
    except Exception as exc:
        raise HTTPException(502, detail=f"Could not fetch JackDash levels: {type(exc).__name__}")
    return {**data, "sort": sort, "cache": cache_status, "cache_ttl_seconds": ttl}


@app.get("/api/v1/levels/recent")
async def recent(page: int = Query(0, ge=0, le=100000)):
    return await levels(sort="recent", page=page)


@app.get("/api/v1/levels/featured")
async def featured(page: int = Query(0, ge=0, le=100000)):
    return await levels(sort="featured", page=page)


@app.get("/api/v1/levels/search")
async def search(q: str = Query(..., min_length=1, max_length=100), page: int = Query(0, ge=0, le=100000)):
    return await levels(sort="recent", page=page, q=q)


@app.get("/api/v1/levels/all")
async def all_levels(page: int = Query(0, ge=0, le=100000)):
    result = await levels(sort="all", page=page)
    result["catalog_note"] = "Paginated upstream listing, not a verified complete export of every JackDash level."
    return result


async def special_level(kind: str):
    endpoint = "getGJDailyLevel.php" if kind == "daily" else "getGJWeeklyLevel.php"
    key = f"special:{kind}"
    async def loader():
        try:
            raw = await post_gd(endpoint, {})
            return parse_special(raw, kind)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (404, 405):
                return {"available": False, "kind": kind, "message": f"JackDash endpoint {endpoint} returned HTTP {exc.response.status_code}", "endpoint": endpoint}
            raise
    try:
        data, status = await cached(key, SPECIAL_TTL, loader)
    except Exception as exc:
        raise HTTPException(502, detail=f"Could not fetch {kind} level: {type(exc).__name__}")
    return {**data, "cache": status, "cache_ttl_seconds": SPECIAL_TTL}


@app.get("/api/v1/levels/daily")
async def daily():
    return await special_level("daily")


@app.get("/api/v1/levels/weekly")
async def weekly():
    return await special_level("weekly")


@app.get("/api/v1/lists")
async def lists():
    key = "dashboard:lists"
    async def loader():
        return await fetch_lists()
    try:
        data, status = await cached(key, LISTS_TTL, loader)
    except Exception as exc:
        raise HTTPException(502, detail=f"Could not fetch JackDash lists page: {type(exc).__name__}")
    return {**data, "cache": status, "cache_ttl_seconds": LISTS_TTL}


@app.get("/api/v1/cache")
async def cache_status():
    now = time.time()
    return {"entries": [{"key": key, "expires_in_seconds": max(0, int(exp - now))} for key, (exp, _) in _cache.items()]}
              
