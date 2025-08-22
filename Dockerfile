--- a/Dockerfile
+++ b/Dockerfile
@@ -1,38 +1,54 @@
 # Base image
 FROM python:3.11-slim
 
 # Prevent Python from writing .pyc files and buffering stdout/stderr
 ENV PYTHONDONTWRITEBYTECODE=1
 ENV PYTHONUNBUFFERED=1
 
 # Set workdir
 WORKDIR /app
 
-#rendered vs static comparision
-RUN pip install playwright
-RUN playwright install --with-deps chromium
-
-# System deps (curl for healthcheck, build tools for lxml)
-RUN apt-get update && apt-get install -y --no-install-recommends \
-    build-essential \ 
-    libxml2-dev libxslt1-dev \ 
-    curl \ 
-    && rm -rf /var/lib/apt/lists/*
+# System deps (Chromium runtime libs for Playwright + build tools for lxml)
+RUN apt-get update && apt-get install -y --no-install-recommends \
+    ca-certificates \
+    curl \
+    build-essential \
+    libxml2-dev libxslt1-dev \
+    \
+    # Chromium runtime libs (Debian package names)
+    libasound2 libatk-bridge2.0-0 libatk1.0-0 libc6 libcairo2 libcups2 \
+    libdbus-1-3 libdrm2 libexpat1 libfontconfig1 libgbm1 libglib2.0-0 \
+    libgtk-3-0 libnss3 libnspr4 libpango-1.0-0 libpangocairo-1.0-0 \
+    libstdc++6 libx11-6 libx11-xcb1 libxcb1 libxcomposite1 libxcursor1 \
+    libxdamage1 libxext6 libxfixes3 libxi6 libxrandr2 libxrender1 \
+    libxss1 libxtst6 \
+    \
+    # Fonts on Debian (avoid Ubuntu-only fonts-ubuntu)
+    fonts-noto-core fonts-noto-cjk fonts-noto-color-emoji \
+    fonts-liberation fonts-dejavu \
+  && rm -rf /var/lib/apt/lists/*
+
+# Playwright (python) and Chromium download (no --with-deps on Debian)
+RUN pip install --no-cache-dir --upgrade pip && \
+    pip install --no-cache-dir "playwright==1.54.0" && \
+    python -m playwright install chromium
 
 # Copy requirements first and install
 COPY requirements.txt ./
-RUN pip install --no-cache-dir -r requirements.txt \ 
-    && pip install --no-cache-dir gunicorn
+RUN pip install --no-cache-dir -r requirements.txt && \
+    pip install --no-cache-dir gunicorn
 
 # Copy project
 COPY . .
 
 # Expose port
 EXPOSE 8000
 
 # Default envs
 ENV TZ=Asia/Kolkata
 
 # Start the app (use $PORT if provided by platform)
 ENV PORT=8000
 CMD exec gunicorn -k uvicorn.workers.UvicornWorker app.main:app --bind 0.0.0.0:${PORT} --workers 2 --timeout 120
