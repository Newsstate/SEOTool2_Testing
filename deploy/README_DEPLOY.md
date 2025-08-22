# Deploying SEO Insight (FastAPI)

This app is a FastAPI project with Jinja2 templates and a SQLite database via SQLModel.

## Option A — One-Click on Render (recommended)

1. Push this folder to a new GitHub repo.
2. Add the two files in the repo root (already generated for you):
   - `render.yaml`
   - `Procfile`
3. On https://render.com → **New +** → **Blueprint** → connect your GitHub repo.
4. Render will read `render.yaml` and set everything up:
   - Build: `pip install -r requirements.txt`
   - Start: `gunicorn -k uvicorn.workers.UvicornWorker app.main:app --bind 0.0.0.0:$PORT --workers 2 --timeout 120`
   - Disk mount at `/var/data` (1 GB) for persistence
   - Environment variables (you’ll fill secrets in the Render UI).
5. Click **Deploy**.

### Persisting the database
By default `app/db.py` uses `sqlite:///seo_insight.db` in the current directory. To keep the DB between deploys, make this tiny change:

```python
# app/db.py
import os
def init_db(db_url: str | None = None):
    global ENGINE
    if db_url is None:
        db_url = os.getenv("DATABASE_URL", "sqlite:///seo_insight.db")
    if ENGINE is None:
        ENGINE = create_engine(db_url, echo=False)
    SQLModel.metadata.create_all(ENGINE)
```

No other changes needed. In Render, `DATABASE_URL` is already set to `sqlite:////var/data/seo_insight.db` via `render.yaml`.

## Option B — Docker (Railway, Fly.io, Cloud Run, etc.)

1. Ensure these files exist in the repo root:
   - `Dockerfile`
   - `.dockerignore`
2. Build and run locally:

```bash
docker build -t seo-insight:latest .
docker run --rm -p 8000:8000 --env-file .env seo-insight:latest
```

3. Deploy to your platform of choice:
   - **Railway**: Create a new project → Deploy from GitHub → Railway detects Dockerfile.
   - **Fly.io**: `fly launch` → `fly deploy`.
   - **Cloud Run**: Push image to Artifact Registry → Create service from image.
   - **Azure App Service / Web Apps for Containers**: Deploy image, set `PORT` env to 8000 (or leave platform default).

## Option C — Heroku (works via third-party providers)
- Add `Procfile` (included) and push to Heroku via GitHub.
- Add a Heroku Postgres add-on or use SQLite on an attached volume (not typical).
- Set env vars in **Settings → Config Vars**.

## Environment Variables
See `.env.example` for all supported keys:
- SMTP_* for email notifications (optional)
- PAGESPEED_API_KEY (optional)
- TZ (optional)
- DATABASE_URL (recommended if you want a persistent DB)

## Static files & templates
- Jinja2 templates are in `app/templates/`. `main.py` already resolves the absolute path.
- No additional static files pipeline is needed.

## Health check
- Root path `/` serves the dashboard page; use it as your platform health check.

## Troubleshooting
- **Import errors**: Ensure you deploy the repo root (where `app/` lives). Start command references `app.main:app`.
- **Template not found**: The app resolves `app/templates` explicitly; don’t move that folder.
- **DB not persisting**: Apply the small `app/db.py` change and set `DATABASE_URL` to a mounted path (e.g., `/var/data/seo_insight.db` on Render).
- **Timeouts on long scans**: Bump `--timeout` in the Procfile/start command or optimize scan concurrency.
