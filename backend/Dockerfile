FROM python:3.12-slim

# Tesseract enables OCR for image-only PDF pages (optional at runtime).
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt requirements-postgres.txt ./
RUN pip install --no-cache-dir -r requirements-postgres.txt

COPY . .
RUN useradd --create-home usi && mkdir -p /data && chown -R usi:usi /app /data
USER usi

ENV USI_DATA_DIR=/data \
    USI_APP_ENV=production \
    PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
