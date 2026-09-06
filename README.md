# WRC Pipeline

A Scrapy pipeline that harvests decisions and determinations from the Irish Workplace Relations Commission site into an immutable landing zone, and a transformation job that derives a clean, current copy of each document from it.
Orchestrated with Dagster as two partitioned assets.

Everything site-specific lives in one YAML file. A source that behaves like this one is a new file; one that differs is a file plus a subclass overriding what differs, and the seams for that (pagination strategies, the document contract) are already in place.

```
source YAML ──► listing spider ──► document fetch ──► landing zone ──► transform ──► transformed zone
                (per facet ×          (fingerprint)     MinIO + Mongo    (BeautifulSoup)  MinIO + Mongo
                 month × page)                          append-only                        one row per doc
```

## Prerequisites

- Docker with Compose
- Python 3.11+

## Setup

```bash
cp .env.example .env                       # defaults work against the compose stack as-is
docker compose up -d                       # MongoDB 7 + MinIO, with healthchecks
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python scripts/check_infra.py              # proves both stores answer; creates the buckets
```

The MinIO console is at <http://localhost:9001>. The credentials are in `.env`.

## Running

Every step is a command with explicit dates. Dates are inclusive and are cut into calendar-aligned partitions (monthly by default). Each record carries the `partition_key` and `partition_date` of the partition that produced it.

### 1. Ingest (scrape into the landing zone)

```bash
scrapy crawl listing -a start_date=2024-01-01 -a end_date=2024-03-31
scrapy crawl listing -a start_date=2024-01-01 -a end_date=2024-01-31 -a facets=3   # one authority
```

Lists every record in the range for each authority, fetches every document, fingerprints it, stores the bytes as received in the `landing` bucket and the metadata in `landing_records`.

Re-running the same range downloads again but stores nothing:
- The content fingerprint is the unique key, so an unchanged document is refused by the index and never overwritten in the bucket.
- A changed document is a new row and a new object so nothing in the landing zone is ever updated.

The run ends with a JSON `run_summary` reconciling what the site declared against what was stored, per partition and per authority. `complete` is true only when every declared record was listed, every listed document fetched, and every fetched document stored or confirmed unchanged.

### 2. Transform (landing zone into the transformed zone)

```bash
python -m wrc.transform --start 2024-01-01 --end 2024-03-31
```

Takes the latest landing version of each document in the range, extracts the title and content with BeautifulSoup into a standalone HTML file (navigation, scripts, styles and comments removed; keeping the decision's markup), passes PDF and other formats through unchanged, renames every file to `<source>/<identifier>.<ext>`, and writes one current row per document to `transformed_records`. Documents whose landing fingerprint has not changed since they were last transformed are skipped. The landing zone is only read. Exits non-zero if any document could not be transformed.

### 3. Orchestrate (Dagster)

`DAGSTER_HOME` must be an absolute path and the same in every shell that touches Dagster.

```bash
export DAGSTER_HOME="$PWD/.dagster"
dagster asset materialize -m wrc.orchestration.definitions --select '*' --partition 2024-01
dagster dev -m wrc.orchestration.definitions      # UI at http://localhost:3000
```

Two monthly-partitioned assets, `landing_documents → transformed_documents`. Partition keys (`2024-01`) are the pipeline's own `partition_key`, so a partition in the UI, in Mongo and in the logs is the same thing. Each asset runs the corresponding command as a subprocess and reads its summary. The counts appear as metadata on the materialisation and a summary that is not `complete` fails the partition so it can be re-materialised on its own. A monthly schedule (`wrc_monthly`, off by default) materialises the partition that just closed.

## Verifying a run

```bash
python -m pytest -q                        # 136 tests; those against the stack skip if it is down
python scripts/recon.py                    # re-verifies every assumption the design rests on
```

`scripts/recon.py` writes `recon/REPORT.md`: page size, statelessness, empty-result states, facet disjointness, document format distribution, whether document bytes are stable between fetches, and robots.txt. It doubles as a contract test against the site: if the markup or behaviour changes, the report names the assumption that broke. `--full` adds a per-year coverage sweep (183 requests) that backs the partition-size choice.

## Configuration

Nothing is hardcoded in the source. `.env` supplies connection strings, bucket and collection names, partition size, the source spec path, and every scraping parameter (concurrency, delay, autothrottle, retries, timeouts, User-Agent, robots.txt). `src/wrc/config.py` validates it at startup so a missing or malformed value fails before any request is made.

`config/sources/wrc.yml` describes the site: request shape and parameter names, the facet it is partitioned by (the four authorities, with the date each became active), the pagination strategy, the listing selectors and result-count patterns, and the document's title andcontent selectors. The transformation reads the same document selectors as the landing fingerprint.

## Logs

JSON, one object per line, with stable keys: `run_planned`, `partition_started` (declared count and expected pages), `record_skipped` (with the missing fields), `record_degraded`, `download_failed` (URL, status, error), `pagination_underrun` and the other pager anomalies, `record_unchanged`, `run_summary`; and for the transform, `document_transformed`, `document_unchanged`, `document_failed`, `transform_summary`.

## Layout

```
config/sources/wrc.yml       everything site-specific
src/wrc/
  config.py                  typed settings from .env
  source.py                  loads a source spec
  parsing.py                 listing parsing, driven by the spec's selectors
  pagination.py              declared_total / follow_next strategies, with loop guards
  partitions.py              calendar-aligned date partitions
  documents.py               fingerprint, slug, extension, object key
  items.py                   DocumentRecord (landing) and TransformedRecord schemas
  stats.py                   per-partition reconciliation and run summaries
  spiders/listing.py         the spider: listing pages, then documents
  pipelines/                 object store then Mongo, append-only
  transform/                 extraction and the transform job
  orchestration/             Dagster definitions
scripts/recon.py             reproducible site reconnaissance
tests/                       unit tests, plus integration tests against the compose stack
```

Nothing outside `spiders/`, `pipelines/` and `orchestration/` imports Scrapy or Dagster.

## Teardown

```bash
docker compose down         # keep data
docker compose down -v      # delete data
```
