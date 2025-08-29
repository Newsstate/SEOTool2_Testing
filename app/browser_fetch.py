from __future__ import annotations
import asyncio, os, re, contextlib
from typing import Optional, Dict, Any, Tuple
from urllib.parse import urlsplit, urlunsplit

from playwright.async_api import async_playwright, Browser, BrowserContext, Page

CONCURRENCY = int(os.environ.get("CONCURRENCY", "4"))
PAGE_TIMEOUT_MS = int(os.environ.get("PAGE_TIMEOUT_MS", "35000"))

class PlaywrightPool:
    def __init__(self) -> None:
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._sema = asyncio.Semaphore(CONCURRENCY)
        self._lock = asyncio.Lock()
        self._ready = asyncio.Event()

    async def start(self) -> None:
        async with self._lock:
            if self._browser:
                self._ready.set()
                return
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-extensions",
                ],
            )
            self._ready.set()

    async def close(self) -> None:
        async with self._lock:
            with contextlib.suppress(Exception):
                if self._browser:
                    await self._browser.close()
            if self._playwright:
                with contextlib.suppress(Exception):
                    await self._playwright.stop()
            self._browser = None
            self._playwright = None
            self._ready.clear()

    async def get_page(self, mobile: bool=False) -> Tuple[BrowserContext, Page]:
        await self._ready.wait()
        assert self._browser is not None
        context = await self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Linux; Android 12; Pixel 6) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/128.0.0.0 Mobile Safari/537.36"
                if mobile else
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            ),
            viewport={"width": 390, "height": 844} if mobile else {"width": 1366, "height": 800},
            device_scale_factor=3 if mobile else 1,
            is_mobile=mobile,
            has_touch=mobile,
            java_script_enabled=True,
            ignore_https_errors=True,
        )
        page = await context.new_page()
        page.set_default_timeout(PAGE_TIMEOUT_MS)
        return context, page

    async def fetch(self, url: str, mobile: bool=False, max_wait_ms: Optional[int]=None) -> Dict[str, Any]:
        timeout = max_wait_ms or PAGE_TIMEOUT_MS
        await self._sema.acquire()
        try:
            context, page = await self.get_page(mobile=mobile)
            try:
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
                # Wait for network to settle—helps with SPA/lazy content
                with contextlib.suppress(Exception):
                    await page.wait_for_load_state("networkidle", timeout=int(timeout*0.6))

                final_url = page.url
                status = resp.status if resp else None
                html = await page.content()

                # Extract performance timings if available
                perf = await page.evaluate("""() => {
                  const t = performance.timing || {};
                  return {
                    domInteractive: (t.domInteractive||0) - (t.navigationStart||0),
                    domComplete: (t.domComplete||0) - (t.navigationStart||0)
                  }
                }""")

                return {
                    "final_url": final_url,
                    "status": status,
                    "html": html,
                    "perf": perf,
                }
            finally:
                with contextlib.suppress(Exception):
                    await page.close()
                with contextlib.suppress(Exception):
                    await context.close()
        finally:
            self._sema.release()

_pool: Optional[PlaywrightPool] = None

async def get_pool() -> PlaywrightPool:
    global _pool
    if _pool is None:
        _pool = PlaywrightPool()
        await _pool.start()
    return _pool

async def fetch_rendered(url: str, mobile: bool=False, max_wait_ms: Optional[int]=None) -> Dict[str, Any]:
    pool = await get_pool()
    return await pool.fetch(url, mobile=mobile, max_wait_ms=max_wait_ms)

async def shutdown_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
    _pool = None
