# Hugging Face Space (Docker SDK) image for Upset Sale Intel.
#
# This is the ONLY build file the Space needs: it clones the app from GitHub and installs Chromium, so the free HF CPU
# tier (16 GB RAM) runs the whole app INCLUDING the browser automation (Delaware C-Track liens + Montgomery UJS
# judgments). Copy this file and README.md into your Space; set secrets in the Space settings. See
# docs/DEPLOY_HUGGINGFACE.md.
#
# Requires the GitHub repo to be public (the clone has no credentials). If you keep it private, see the runbook for the
# token variant.

ARG REPO=https://github.com/Aradevski170802/ReaLTOR.git

# ---- Build the web UI ----
FROM node:22-alpine AS web
ARG REPO
ARG CACHEBUST=1
RUN apk add --no-cache git && git clone --depth 1 "$REPO" /src
WORKDIR /src/frontend
RUN npm ci && npm run build   # -> /src/frontend/dist

# ---- App image (Chromium + Python) ----
FROM python:3.12-slim
ARG REPO
ARG CACHEBUST=1
ENV PYTHONUNBUFFERED=1 \
    USI_APP_ENV=production \
    USI_BROWSER_AUTOMATION=true \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    USI_DATA_DIR=/data \
    HOME=/home/user \
    PORT=8000
RUN apt-get update \
    && apt-get install -y --no-install-recommends git tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 "$REPO" /app
WORKDIR /app/backend
# requirements.txt = core, -browser = Playwright, -postgres = psycopg (for an optional external database)
RUN pip install --no-cache-dir -r requirements.txt -r requirements-browser.txt -r requirements-postgres.txt \
    && python -m playwright install --with-deps chromium
COPY --from=web /src/frontend/dist /app/frontend/dist
# HF Spaces run the container as UID 1000; make that user own the app, the data dir and the browsers.
RUN useradd -m -u 1000 user && mkdir -p /data && chown -R user:user /data /app /ms-playwright /home/user
USER user
EXPOSE 8000
CMD ["python", "scripts/serve.py"]
