# app/main.py
from __future__ import annotations

import sys
import os
import asyncio
from time import time
from typing import Dict, Tuple, Any, Optional
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, quote

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Form, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, HttpUrl

from .browser_fetch import shutdown_pool
from .seo import analyze as analyze_url
from .db import init_db, save_analysis

# ---- Windows asyncio policy fix (safe no-op elsewhere)
if sys.platform.startswith("win"):
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except Exception:
        pass

# ---- Lifespan handles startup/shutdown cleanly
@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup
    init_db()
    yield
    # shutdown
    await shutdown_pool()

app = FastAPI(title="SEO Analyzer", lifespan=lifespan)

# ---- Templates dir (app/templates by default; overridable via env)
BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = Path(os.getenv("TEMPLATES_DIR") or (BASE_DIR / "templates"))
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# ---- Simple cache for AMP compare (best-effort)
COMPARE_CACHE: Dict[str, Tuple[float, dict]] = {}
COMPARE_TTL = 15 * 60  # seconds


def _val(d: Any, *path: str, default=None):
    cur = d
    for p in path:
        if isinstance(cur, dict):
            cur = cur.get(p)
        else:
            return default
        if cur is None:
            return default
    return cur if cur not in (None, "") else default


def _yesno(b: Any) -> str:
    return "Yes" if bool(b) else "No"


def _norm_url(url: str) -> str:
    """Normalize URL for caching/redirects (add scheme, lowercase host, strip fragment)."""
    if not url:
        return url
    u = url.strip()
    if u.startswith("//"):
        u = "https:" + u
    elif not u.startswith("http://") and not u.startswith("https://"):
        u = "https://" + u
    parts = urlsplit(u)
    netloc = parts.netloc.lower()
    return urlunsplit((parts.scheme, netloc, parts.path or "/", parts.query, ""))


async def build_amp_compare_payload(url: str, request: Request | None):
    """
    Returns dict suitable for amp_compare.html:
    { request, url, amp_url, rows, error }
    """
    base = await analyze_url(url)
    amp_url = base.get("amp_url")
    if not amp_url:
        return {
            "request": request,
            "url": url,
            "amp_url": None,
            "rows": [],
            "error": "No AMP version found via <link rel='amphtml'>.",
        }

    amp = await analyze_url(amp_url)

    rows = [
        {
            "label": "Title",
            "non_amp": _val(base, "title", default="—"),
            "amp": _val(amp, "title", default="—"),
            "changed": _val(base, "title") != _val(amp, "title"),
        },
        {
            "label": "Meta Description",
            "non_amp": _val(base, "description", default="—"),
            "amp": _val(amp, "description", default="—"),
            "changed": _val(base, "description") != _val(amp, "description"),
        },
        {
            "label": "Canonical",
            "non_amp": _val(base, "canonical", default="—"),
            "amp": _val(amp, "canonical", default="—"),
            "changed": _val(base, "canonical") != _val(amp, "canonical"),
        },
        {
            "label": "Robots Meta",
            "non_amp": _val(base, "robots_meta", default="—"),
            "amp": _val(amp, "robots_meta", default="—"),
            "changed": _val(base, "robots_meta") != _val(amp, "robots_meta"),
        },
        {
            "label": "H1 Count",
            "non_amp": len(_val(base, "headings", "h1", default=[]) or []),
            "amp": len(_val(amp, "headings", "h1", default=[]) or []),
            "changed": len(_val(base, "headings", "h1", default=[]) or [])
            != len(_val(amp, "headings", "h1", default=[]) or []),
        },
        {
            "label": "First H1",
            "non_amp": (_val(base, "headings", "h1", default=[None]) or [None])[0] or "—",
            "amp": (_val(amp, "headings", "h1", default=[None]) or [None])[0] or "—",
            "changed": (_val(base, "headings", "h1", default=[None]) or [None])[0]
            != (_val(amp, "headings", "h1", default=[None]) or [None])[0],
        },
        {
            "label": "Open Graph present",
            "non_amp": _yesno(base.get("has_open_graph")),
            "amp": _yesno(amp.get("has_open_graph")),
            "changed": bool(base.get("has_open_graph")) != bool(amp.get("has_open_graph")),
        },
        {
            "label": "Twitter Card present",
            "non_amp": _yesno(base.get("has_twitter_card")),
            "amp": _yesno(amp.get("has_twitter_card")),
            "changed": bool(base.get("has_twitter_card")) != bool(amp.get("has_twitter_card")),
        },
    ]

    return {
        "request": request,
        "url": url,
        "amp_url": amp_url,
        "rows": rows,
        "error": None,
    }


def _compare_cache_put(url: str, payload: dict):
    COMPARE_CACHE[url] = (time(), payload)


def _compare_cache_get(url: str) -> dict | None:
    hit = COMPARE_CACHE.get(url)
    if not hit:
        return None
    ts, payload = hit
    if time() - ts > COMPARE_TTL:
        return None
    return payload


async def _warm_compare_async(url: str):
    try:
        payload = await build_amp_compare_payload(url, request=None)
        _compare_cache_put(url, payload)
    except Exception:
        pass


# ---- Pages
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "result": None})


# POST/Redirect/GET: avoids 405 for GET /analyze
@app.post("/analyze")
async def analyze_post(url: str = Form(...)):
    norm = _norm_url(url)
    return RedirectResponse(url=f"/analyze?url={quote(norm)}", status_code=303)


@app.get("/analyze", response_class=HTMLResponse)
@app.get("/analyze/", response_class=HTMLResponse)
async def analyze_get(request: Request, url: Optional[str] = Query(None)):
    if not url:
        return templates.TemplateResponse("index.html", {"request": request, "result": None})

    norm = _norm_url(url)
    try:
        result = await analyze_url(norm)

        # Persist to DB
        save_analysis(
            url=norm,
            result=result,
            status_code=int(result.get("status_code") or 0),
            load_time_ms=int(result.get("load_time_ms") or 0),
            content_length=int(result.get("content_length") or 0),
            is_amp=bool(result.get("is_amp")),
        )

        if result.get("amp_url"):
            asyncio.create_task(_warm_compare_async(result["url"]))

        return templates.TemplateResponse("index.html", {"request": request, "result": result})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---- API
class AnalyzeQuery(BaseModel):
    url: HttpUrl


@app.get("/api/analyze", response_class=JSONResponse)
async def api_analyze(url: HttpUrl):
    norm = _norm_url(str(url))
    try:
        result = await analyze_url(norm)

        save_analysis(
            url=norm,
            result=result,
            status_code=int(result.get("status_code") or 0),
            load_time_ms=int(result.get("load_time_ms") or 0),
            content_length=int(result.get("content_length") or 0),
            is_amp=bool(result.get("is_amp")),
        )

        if result.get("amp_url"):
            asyncio.create_task(_warm_compare_async(result["url"]))

        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ---- AMP vs Non-AMP comparison page
@app.get("/amp-compare", response_class=HTMLResponse)
async def amp_compare(request: Request, url: str):
    cached = _compare_cache_get(url)
    if cached:
        payload = dict(cached)
        payload["request"] = request
        return templates.TemplateResponse("amp_compare.html", payload)

    payload = await build_amp_compare_payload(url, request)
    _compare_cache_put(url, dict(payload, request=None))
    return templates.TemplateResponse("amp_compare.html", payload)
