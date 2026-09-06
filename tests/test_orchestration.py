"""Dagster wiring, with the subprocesses replaced by fakes; the real jobs have their own tests."""

import json
from datetime import date

import pytest
from dagster import Failure, build_asset_context, materialize

from wrc.orchestration import definitions as d


def test_partition_keys_match_the_pipelines_partition_key_format():
    keys = d.monthly.get_partition_keys(current_time=__import__("datetime").datetime(2024, 4, 15))
    assert keys[:3] == ["2024-01", "2024-02", "2024-03"]


def test_window_dates_are_inclusive_and_calendar_aligned():
    context = build_asset_context(partition_key="2024-02")
    assert d.window_dates(context) == (date(2024, 2, 1), date(2024, 2, 29))


def test_find_summary_picks_the_right_json_line_out_of_mixed_output():
    output = "\n".join([
        "2026-09-06 INFO scrapy.core.engine: Spider opened",
        json.dumps({"event": "partition_started", "records_found": 45}),
        "not json {",
        json.dumps({"event": "run_summary", "complete": True, "records_found": 45}),
    ])
    assert d.find_summary(output, "run_summary")["records_found"] == 45
    assert d.find_summary(output, "transform_summary") is None


def test_assets_run_in_dependency_order_and_expose_summaries_as_metadata(monkeypatch):
    calls = []

    def fake_run(argv, capture_output, text):
        calls.append(argv)
        event = "run_summary" if "scrapy" in argv else "transform_summary"
        body = {"event": event, "complete": True, "records_found": 45, "documents_stored": 45, "candidates": 45, "transformed": 45}
        return type("P", (), {"returncode": 0, "stdout": json.dumps(body), "stderr": ""})()

    monkeypatch.setattr(d.subprocess, "run", fake_run)
    result = materialize([d.landing_documents, d.transformed_documents], partition_key="2024-01")
    assert result.success
    assert [("scrapy" in c) for c in calls] == [True, False], "landing before transformed"
    assert "start_date=2024-01-01" in calls[0] and "end_date=2024-01-31" in calls[0]
    assert calls[1][-4:] == ["--start", "2024-01-01", "--end", "2024-01-31"]
    landing = result.asset_materializations_for_node("landing_documents")[0]
    assert landing.metadata["records_found"].value == 45


def test_incomplete_summary_fails_the_partition(monkeypatch):
    def fake_run(argv, capture_output, text):
        body = {"event": "run_summary", "complete": False, "records_unaccounted": 1, "incomplete_partitions": [{"partition": "2024-01"}]}
        return type("P", (), {"returncode": 0, "stdout": json.dumps(body), "stderr": ""})()

    monkeypatch.setattr(d.subprocess, "run", fake_run)
    result = materialize([d.landing_documents], partition_key="2024-01", raise_on_error=False)
    assert not result.success


def test_missing_summary_fails_with_the_output_tail(monkeypatch):
    def fake_run(argv, capture_output, text):
        return type("P", (), {"returncode": 1, "stdout": "", "stderr": "Traceback: boom"})()

    monkeypatch.setattr(d.subprocess, "run", fake_run)
    with pytest.raises(Failure, match="without a run_summary line"):
        d.run_job(build_asset_context(partition_key="2024-01"), ["scrapy"], "run_summary")


def test_definitions_load_with_job_and_schedule():
    assert {a.key.to_user_string() for a in d.defs.assets} == {"landing_documents", "transformed_documents"}
    assert d.defs.resolve_job_def("wrc_pipeline") is not None
    assert d.defs.resolve_schedule_def("wrc_monthly").cron_schedule == "0 0 1 * *"
