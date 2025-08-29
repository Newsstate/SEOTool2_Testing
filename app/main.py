from __future__ import annotations
import os, asyncio, time
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, HttpUrl

from .browser_fetch import fetch_rendered, get_pool, shutdown_pool
from .seo import parse_seo

templates = Jinja2Templates(directory="app/templates")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Warm up Playwright
    await get_pool()
    yield
    await shutdown_pool()

app = FastAPI(title="SEO Scanner (Playwright)", lifespan=lifespan)

class ScanIn(BaseModel):
    url: HttpUrl
    mobile: Optional[bool] = False
    max_wait_ms: Optional[int] = None

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/api/analyze")
async def analyze(
    request: Request,
    url: str = Form(None),
    mobile: Optional[bool] = Form(False),
    max_wait_ms: Optional[int] = Form(None),
):
    # Support JSON body as well
    if not url:
        try:
            data = await request.json()
            url = data.get("url")
            mobile = data.get("mobile", False)
            max_wait_ms = data.get("max_wait_ms")
        except Exception:
            return JSONResponse({"error": "Missing url"}, status_code=400)

    t0 = time.time()
    try:
        rendered = await fetch_rendered(url, mobile=bool(mobile), max_wait_ms=max_wait_ms)
        html = rendered.get("html", "")
        final_url = rendered.get("final_url", url)
        status = rendered.get("status")
        perf = rendered.get("perf", {})

        seo = parse_seo(html, final_url)

        out = {
            "input_url": url,
            "final_url": final_url,
            "http_status": status,
            "timing_ms": int((time.time() - t0) * 1000),
            "perf": perf,
            **seo,
        }
        return JSONResponse(out)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
