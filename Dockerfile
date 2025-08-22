# Base image
FROM python:3.11-slim

# Prevent Python from writing .pyc files and buffering stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set workdir
WORKDIR /app

# System deps (Chromium runtime + build tools for lxml + curl/CA)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    build-essential \
    libxml2-dev libxslt1-dev \
    \
    # Chromium runtime libs (Debian package names)
    libasound2 libatk-bridge2.0-0 libatk1.0-0 libc6 libcairo2 libcups2 \
    libdbus-1-3 libdrm2 libexpat1 libfontconfig1 libgbm1 libglib2.0-0 \
    libgtk-3-0 libnss3 libnspr4 libpango-1.0-0 libpangocairo-1.0-0 \
    libstdc++6 libx11-6 libx11-xcb1 libxcb1 libxcomposite1 libxcursor1 \
    libxdamage1 libxext6 libxfixes3 libxi6 libxrandr2 libxrender1 \
    libxss1 libxtst6 \
    \
    # Fonts (Debian names; avoid Ubuntu-only fonts-ubuntu)
    fonts-noto-core fonts-noto-cjk fonts-noto-color-emoji \
    fonts-liberation fonts-dejavu \
  && rm -rf /var/lib/apt/lists/*

# Python deps: install Playwright first, then fetch Chromium (no --with-deps)
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir "playwright==1.54.0" && \
    python -m playwright install chromium

# App dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir gunicorn

# Copy project
COPY . .

# Expose port
EXPOSE 8000

# Default envs
ENV TZ=Asia/Kolkata
ENV PORT=8000

# Start the app
CMD exec gunicorn -k uvicorn.workers.UvicornWorker app.main:app --bind 0.0.0.0:${PORT} --workers 2 --timeout 120
