from __future__ import annotations

import os
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Dict, Optional, Tuple, List

from playwright.async_api import async_playwright, Browser, BrowserContext, Page

# ---- wait_until normalization ----
_ALLOWED_WAITS = {"load", "domcontentloaded", "networkidle", "commit"}
_WAIT_ALIASES = {
    # DOM loaded variants
    "dom": "domcontentloaded",
    "dom_loaded": "domcontentloaded",
    "domcontent": "domcontentloaded",
    "dom-content-loaded": "domcontentloaded",
    "domcontentloaded": "domcontentloaded",
    "documentloaded": "domcontentloaded",
    "document_loaded": "domcontentloaded",
    "domcontentloaded()": "domcontentloaded",
    "domcontentLoaded": "domcontentloaded",
    "domContentLoaded": "domcontentloaded",

    # network idle variants / typos
    "network_idle": "networkidle",
    "network-idle": "networkidle",
    "networkidle()": "networkidle",
    "networkIdle": "networkidle",
    "idle": "networkidle",
}

def _normalize_wait_until(val: Optional[str], default: str = "networkidle") -> str:
    v = (val or "").strip().lower()
    v = _WAIT_ALIASES.get(v, v)
    return v if v in _ALLOWED_WAITS else default

_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


@dataclass
class RenderResult:
    status: Optional[int]
    final_url: str
    headers: Dict[str, str]
    html: str
    console_logs: List[str]
    timing_ms: int
    screenshot_path: Optional[str] = None


class _BrowserPool:
    """Lightweight Playwright Chromium pool for concurrent scans."""
    def __init__(self, headless: bool = True, max_contexts: int = 4):
        self._headless = headless
        self._max = max_contexts
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._sem = asyncio.Semaphore(max_contexts)
        self._started = False

    async def start(self):
        if self._started:
            return
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(headless=self._headless)
        self._playwright = pw
        self._browser = browser
        self._started = True

    async def stop(self):
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        self._browser = None
        self._playwright = None
        self._started = False

    @asynccontextmanager
    async def page(self, **context_kwargs):
        if not self._started:
            await self.start()
        async with self._sem:
            context: BrowserContext = await self._browser.new_context(**context_kwargs)
            page: Page = await context.new_page()
            logs: List[str] = []
            page.on("console", lambda msg: logs.append(msg.text()))
            try:
                yield page, logs
            finally:
                await context.close()


_POOL = _BrowserPool(
    headless=(os.getenv("PLAYWRIGHT_HEADLESS", "1").lower() not in ("0", "false", "no")),
    max_contexts=int(os.getenv("PLAYWRIGHT_MAX_CONTEXTS", "4")),
)


async def fetch_rendered(
    url: str,
    *,
    wait_until: Optional[str] = None,   # allow None → read env/default
    wait_ms_after: int = 800,
    timeout_ms: int = 30000,
    user_agent: str = _DEFAULT_UA,
    viewport: Tuple[int, int] = (1366, 768),
    screenshot: bool = False,
) -> RenderResult:
    from time import monotonic

    # Normalize wait_until from arg/env
    env_wait = os.getenv("PLAYWRIGHT_WAIT_UNTIL", "networkidle")
    wait_until = _normalize_wait_until(wait_until or env_wait, default="networkidle")

    start = monotonic()
    async with _POOL.page(
        user_agent=user_agent,
        viewport={"width": viewport[0], "height": viewport[1]},
    ) as (page, logs):
        try:
            resp = await page.goto(url, wait_until=wait_until, timeout=timeout_ms)
        except Exception:
            # last-resort fallback if a bad token slipped through somehow
            resp = await page.goto(url, wait_until="networkidle", timeout=timeout_ms)

        # Nudge lazy content
        for _ in range(3):
            await page.mouse.wheel(0, 1200)
            await asyncio.sleep(0.2)

        if wait_ms_after:
            await asyncio.sleep(wait_ms_after / 1000)

        # Heuristic: wait for common content containers if they appear quickly
        try:
            await page.wait_for_selector("main, article, #content, .content", timeout=1500)
        except Exception:
            pass

        html = await page.content()
        status = resp.status if resp else None
        final_url = page.url

        headers: Dict[str, str] = {}
        try:
            if resp:
                headers = {k.lower(): v for k, v in (await resp.all_headers()).items()}
        except Exception:
            headers = {}

        shot = None
        if screenshot:
            shot = f"/tmp/playwright_{abs(hash(final_url))}.png"
            await page.screenshot(path=shot, full_page=True)

    end = monotonic()
    return RenderResult(
        status=status,
        final_url=final_url,
        headers=headers,
        html=html,
        console_logs=logs,
        timing_ms=int((end - start) * 1000),
        screenshot_path=shot,
    )


async def shutdown_pool():
    await _POOL.stop()
