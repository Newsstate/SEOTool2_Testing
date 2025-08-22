# app/browser_fetch.py
if not self._started:
await self.start()
async with self._sem:
context: BrowserContext = await self._browser.new_context(**context_kwargs)
page: Page = await context.new_page()
# Capture console for debugging
logs: List[str] = []
page.on("console", lambda msg: logs.append(msg.text()))
try:
yield page, logs
finally:
await context.close()


_pool = _BrowserPool()


async def fetch_rendered(
url: str,
*,
wait_until: str = "networkidle", # 'load' | 'domcontentloaded' | 'networkidle'
wait_ms_after: int = 500, # to settle lazy content
timeout_ms: int = 30000,
user_agent: str = _DEFAULT_UA,
viewport: Tuple[int, int] = (1366, 768),
screenshot: bool = False,
) -> RenderResult:
"""Navigate with Playwright and return fully rendered DOM + metadata."""
from time import monotonic
start = monotonic()


async with _pool.page(user_agent=user_agent, viewport={"width": viewport[0], "height": viewport[1]}) as (page, logs):
resp = await page.goto(url, wait_until=wait_until, timeout=timeout_ms)
# Progressive scroll to trigger lazy-loading
for _ in range(3):
await page.mouse.wheel(0, 1200)
await asyncio.sleep(0.2)
if wait_ms_after:
await asyncio.sleep(wait_ms_after/1000)


# Optional: wait for main content heuristic if present
try:
await page.wait_for_selector("main, article, #content, .content", timeout=1500)
except Exception:
pass


html = await page.content()
status = None if resp is None else resp.status
final_url = page.url
headers = {}
try:
headers = {} if resp is None else {k.lower(): v for k, v in (await resp.all_headers()).items()}
except Exception:
headers = {}


shot = None
if screenshot:
shot = f"/mnt/data/playwright_shot_{abs(hash(final_url))}.png"
await page.screenshot(path=shot, full_page=True)


end = monotonic()
return RenderResult(
status=status,
final_url=final_url,
headers=headers,
html=html,
console_logs=logs,
timing_ms=int((end-start)*1000),
screenshot_path=shot,
)


async def shutdown_pool():
await _pool.stop()