# AI Smart Shopper: production image (gunicorn + WhiteNoise). Build: docker build -t ai-smart-shopper .
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_ENV=production \
    PORT=8000

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
RUN chmod +x docker-entrypoint.sh \
    && useradd --create-home --uid 1000 shopper \
    && mkdir -p instance app/logs app/static/uploads \
    && chown -R shopper:shopper /app
USER shopper

EXPOSE 8000
# Exit code 1 only when the database is down (see app/services/health.py)
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 CMD ["flask", "--app", "run", "healthcheck"]

ENTRYPOINT ["./docker-entrypoint.sh"]
