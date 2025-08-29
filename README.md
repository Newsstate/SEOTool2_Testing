# SEO Scanner (Playwright-rendered)

A FastAPI-based SEO scanner that fetches pages with **Playwright (Chromium)** to render JavaScript and analyze the fully rendered DOM. Built to run on Render.

## Features
- Headless Chromium via Playwright for accurate, JS-rendered DOM
- Connection/session pool with bounded concurrency
- Robust wait strategy: `domcontentloaded` + network idle
- Extracts core SEO signals (title, meta, canonicals, robots, hreflang, headings, links, schema types)
- Simple HTML UI + `/api/analyze` JSON API
- Production-ready on Render with `render.yaml`

## Deploy on Render
1. Push this repo to GitHub.
2. Create a **Web Service** on Render, point to this repo.
3. Ensure the following:
   - **Build Command**
     ```bash
     pip install -r requirements.txt
     python -m playwright install --with-deps chromium
     ```
   - **Start Command**
     ```bash
     uvicorn app.main:app --host 0.0.0.0 --port $PORT
     ```
4. Optional env vars (sane defaults included in `render.yaml`):
   - `CONCURRENCY=4` (pages processed concurrently)
   - `PAGE_TIMEOUT_MS=35000` (per-page timeout)

## Local Dev
```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
uvicorn app.main:app --reload
```

Visit `http://127.0.0.1:8000`

## API
`POST /api/analyze` with form data or JSON:
```json
{
  "url": "https://example.com",
  "mobile": true,
  "max_wait_ms": 35000
}
```

Response (truncated example):
```json
{
  "final_url": "...",
  "timing_ms": 3120,
  "http_status": 200,
  "title": "...",
  "meta": {"description":"...", "robots":"...", ...},
  "links": {"internal": 123, "external": 45, "nofollow": 8},
  "headings": {"h1":["..."], "h2":[...]},
  "schema_types": ["Organization", "BreadcrumbList"],
  "issues": ["Missing meta description"]
}
```

## Notes
- On free tiers, Playwright can be memory-heavy. Keep `CONCURRENCY` modest (2–4).
- If pages heavily lazy-load, bump `max_wait_ms` or `wait_until`.
- If you need screenshots/trace for debugging, toggle in `browser_fetch.py`.
