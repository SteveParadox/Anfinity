FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app \
    PORT=8000

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    curl \
    libgomp1 \
    libmagic1 \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /app/certs && \
    curl -fsSL https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem \
    -o /app/certs/global-bundle.pem

COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

COPY alembic.ini .
COPY app/ ./app/
COPY run.py .

RUN addgroup --system anfinity \
    && adduser --system --ingroup anfinity anfinity \
    && chown -R anfinity:anfinity /app

USER anfinity

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.getenv('PORT', '8000'), timeout=5).read()" || exit 1

CMD ["python", "run.py"]