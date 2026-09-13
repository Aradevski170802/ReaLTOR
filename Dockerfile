# Single-container image: built web UI + API + in-process background worker.
# Used by render.yaml; also runs anywhere Docker runs:
#   docker build -t upset-sale-intel .
#   docker run -p 8000:8000 -e USI_MASTER_KEY=<long random string> -e USI_SITE_PASSWORD=<password> -e USI_SEED_DEMO=true upset-sale-intel

FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
# Tesseract enables OCR for image-only PDF pages.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./
COPY --from=web /web/dist /app/frontend/dist
RUN useradd --create-home usi && mkdir -p /app/data && chown -R usi:usi /app
USER usi

ENV PYTHONUNBUFFERED=1 \
    USI_APP_ENV=production \
    USI_DATA_DIR=/app/data \
    PORT=8000

EXPOSE 8000
CMD ["python", "scripts/serve.py"]
