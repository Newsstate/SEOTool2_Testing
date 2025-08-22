# Base image
FROM python:3.11-slim

# Prevent Python from writing .pyc files and buffering stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set workdir
WORKDIR /app

# System deps for Playwright/Chromium + build tools for lxml + curl
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    build-essential \
    libxml2-dev libxslt1-dev \
    # Chromium runtime libs
    libasound2 libatk-bridge2.0-0 libatk1.0-0 libc6 libcairo2 libcups2 \
    libdbus-1-3 libdrm2 libexpat1 libfontconfig1 libgbm1 libglib2.0-0 \
    libgtk-3-0 libnss3 libnspr4 libpango-1.0-0 libpangocairo-1.0-0 \
    libstdc++6 libx11-6 libx11-xcb1 libxcb1 libxcomposite1 libxcursor1 \
    libxdamage1 libxext6 libxfixes3 libxi6 libxrandr2 libxrender1 \
    libxss1 libxtst6 \
    # Fonts on Debian (NOT the ttf- packages)
    fonts-unifont fonts-ubuntu fonts-liberation fonts-dejavu \
    fonts-noto-color-emoji \
  && rm -rf /var/lib/apt/lists/*

# Install Python deps (playwright first so browsers install succeeds)
# If playwright is already in your requirements.txt, you can drop the explicit install line.
RUN pip install --no-cache-dir --upgrade pip
RUN pip install --no-cache-dir "playwright==1.54.0"

# Install Chromium browser binaries (no --with-deps on Debian)
RUN python -m playwright install chromium

# Copy requirements and install the rest
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
