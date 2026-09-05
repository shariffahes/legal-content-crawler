# WRC Pipeline

Scrapy ingestion and transformation pipeline for decisions and determinations published by the Irish Workplace Relations Commission.

## Prerequisites

- Docker + Docker Compose
- Python 3.11+

## Setup

```bash
cp .env.example .env          # adjust credentials/ports if needed
docker compose up -d          # MongoDB + MinIO
python -m venv .venv && source .venv/bin/activate
pip install -e .
python scripts/check_infra.py # verifies connectivity and creates buckets
```

MinIO console: [http://localhost:9001](http://localhost:9001) (credentials from `.env`).

## Configuration

All connection strings, storage locations and scraping parameters are supplied by environment variables, loaded from `.env` and validated at startup by `src/wrc/config.py`. See `.env.example` for the full set.

## Teardown

```bash
docker compose down       # stop containers, keep data
docker compose down -v    # stop containers and delete all stored data
```

