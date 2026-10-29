FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

WORKDIR /app

RUN apt-get update \
    && apt-get install --no-install-recommends -y libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt

COPY backend/requirements.txt ./backend-requirements.txt
RUN python -m pip install -r backend-requirements.txt

COPY . .
RUN mkdir -p /app/data/documents /app/chroma_db /app/logs \
    && sed -i 's/\r$//' /app/infra/backend-entrypoint.sh \
    && chmod +x /app/infra/backend-entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/app/infra/backend-entrypoint.sh"]
