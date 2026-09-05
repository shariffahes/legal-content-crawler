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

## Reconnaissance

`scripts/recon.py` re-verifies every empirical claim the design rests on and writes
`recon/REPORT.md`. It doubles as a contract test against the source site: if the markup or
behaviour changes, the report names the assumption that broke.

```bash
python scripts/recon.py          # 42 requests, ~45s
python scripts/recon.py --full   # adds the per-year coverage sweep: 183 requests, ~4min
```

Requests are issued sequentially over one session with a configurable delay; the report states
the request count so the politeness posture is measured rather than asserted.

## Configuration

All connection strings, storage locations and scraping parameters are supplied by environment variables, loaded from `.env` and validated at startup by `src/wrc/config.py`. See `.env.example` for the full set.

## Teardown

```bash
docker compose down       # stop containers, keep data
docker compose down -v    # stop containers and delete all stored data
```

