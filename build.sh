#!/usr/bin/env bash
set -euo pipefail

# 1) Install Python deps (playwright, gunicorn, dotenv are in requirements.txt)
pip install -r requirements.txt

# 2) Download the Chromium browser for Playwright
python -m playwright install chromium
