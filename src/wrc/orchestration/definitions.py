import json
import subprocess
import sys
from datetime import date, timedelta

from dagster import (
    AssetExecutionContext,
    AssetSelection,
    Definitions,
    Failure,
    MaterializeResult,
    MetadataValue,
    MonthlyPartitionsDefinition,
    asset,
    build_schedule_from_partitioned_job,
    define_asset_job,
)

from wrc.config import get_settings

settings = get_settings()

monthly = MonthlyPartitionsDefinition(start_date=settings.orchestration_start_partition, fmt="%Y-%m")


def window_dates(context: AssetExecutionContext) -> tuple[date, date]:
    window = monthly.time_window_for_partition_key(context.partition_key)
    return window.start.date(), (window.end - timedelta(days=1)).date()


def find_summary(output: str, event: str) -> dict | None:
    for line in output.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if record.get("event") == event:
            return record
    return None


def run_job(context: AssetExecutionContext, argv: list[str], summary_event: str) -> dict:
    """Run a pipeline CLI and return its summary, failing the step if it is not complete."""
    context.log.info("running: %s", " ".join(argv))
    proc = subprocess.run(argv, capture_output=True, text=True)
    output = proc.stdout + "\n" + proc.stderr
    summary = find_summary(output, summary_event)

    if summary is None:
        raise Failure(
            description=f"{argv[0]} exited {proc.returncode} without a {summary_event} line",
            metadata={"tail": MetadataValue.text(output[-4000:])},
        )
    context.log.info("%s: %s", summary_event, json.dumps({k: v for k, v in summary.items() if k not in ("ts", "logger")}))
    if not summary.get("complete"):
        raise Failure(
            description=f"{summary_event} reports incomplete",
            metadata={key: MetadataValue.json(value) if isinstance(value, (dict, list)) else value
                      for key, value in summary.items() if key not in ("ts", "logger", "level", "event")},
        )
    return summary


def summary_metadata(summary: dict, keys: tuple[str, ...]) -> dict:
    return {key: summary[key] for key in keys if key in summary}


@asset(partitions_def=monthly, group_name="wrc", description="Documents and metadata scraped into the landing zone.")
def landing_documents(context: AssetExecutionContext) -> MaterializeResult:
    start, end = window_dates(context)
    summary = run_job(
        context,
        [sys.executable, "-m", "scrapy", "crawl", "listing",
         "-a", f"start_date={start}", "-a", f"end_date={end}"],
        "run_summary",
    )
    return MaterializeResult(
        metadata=summary_metadata(summary, (
            "records_found", "records_scraped", "records_skipped", "records_degraded", "records_duplicate",
            "documents_downloaded", "downloads_failed", "documents_stored", "documents_unchanged", "stores_failed",
            "records_unaccounted", "partitions_processed",
        ))
    )


@asset(
    partitions_def=monthly, group_name="wrc", deps=[landing_documents],
    description="Content extracted from landing documents into the transformed zone.",
)
def transformed_documents(context: AssetExecutionContext) -> MaterializeResult:
    start, end = window_dates(context)
    summary = run_job(
        context,
        [sys.executable, "-m", "wrc.transform", "--start", str(start), "--end", str(end)],
        "transform_summary",
    )
    return MaterializeResult(
        metadata=summary_metadata(summary, ("candidates", "transformed", "passthrough", "unchanged", "failed"))
    )


pipeline_job = define_asset_job(
    "wrc_pipeline",
    selection=AssetSelection.assets(landing_documents, transformed_documents),
)

# Runs once a month for the partition that just closed; older partitions are backfills.
monthly_schedule = build_schedule_from_partitioned_job(pipeline_job, name="wrc_monthly")

defs = Definitions(assets=[landing_documents, transformed_documents], jobs=[pipeline_job], schedules=[monthly_schedule])
