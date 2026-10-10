import boto3
import pytest

from core.telemetry import RunTelemetry
from db.collection_state import event_work_page, get_checkpoint
from db.schema_collection import collection_state_key, event_work_key
from scraper.dispatcher import CollectionFailure, collect_sources
from tests.source_helpers import INDEX, real_inputs


class PagedClient:
    def __init__(self, pages, capture, fail_page=None):
        self.pages = pages
        self.capture = capture
        self.fail_page = fail_page
        self.calls = []

    def get_tournaments(self, **kwargs):
        page = kwargs["page"]
        self.calls.append(page)
        if page == self.fail_page:
            raise RuntimeError("page failed")
        return self.pages.get(page, [])

    def capture_tournament(self, event):
        return self.capture.model_copy(update={"source_event_id": event})


def run(table, bucket, client, admission, **kwargs):
    return collect_sources(
        admissions=[admission],
        catalog_index=INDEX,
        s3_client=boto3.client("s3"),
        bucket_name=bucket.name,
        clients={"limitless": client},
        table=table,
        **kwargs,
    )


def test_discovery_commits_each_nonempty_page_and_replays_failed_page(dynamodb_table, s3_bucket):
    capture, admission, _ = real_inputs("limitless")
    admission = admission.model_copy(update={"enabled": True})
    fmt = next(iter(admission.format_mappings))
    admission = admission.model_copy(
        update={"format_mappings": {fmt: admission.format_mappings[fmt]}}
    )
    client = PagedClient({1: [{"id": capture.source_event_id}], 2: [{"id": "second"}]}, capture, 2)

    class Metrics:
        def put_metric_data(self, **kwargs):
            pass

    telemetry = RunTelemetry("scraper", client=Metrics())
    with pytest.raises(CollectionFailure):
        run(dynamodb_table, s3_bucket, client, admission, page_size=1, telemetry=telemetry)
    assert telemetry.counts["DiscoveryPages"] == 1
    assert telemetry.counts["DiscoveredEvents"] == 1
    state = get_checkpoint(dynamodb_table, collection_state_key("limitless", "discover", fmt))
    assert state.page == 1
    assert state.status == "failed" and state.error == "RuntimeError"
    assert dynamodb_table.get_item(
        Key=event_work_key("limitless", capture.source_event_id, "capture")
    ).get("Item")
    client.fail_page = None
    run(dynamodb_table, s3_bucket, client, admission, page_size=1)
    assert client.calls == [1, 2, 2, 3]
    assert (
        get_checkpoint(dynamodb_table, collection_state_key("limitless", "discover", fmt)).page == 3
    )
    assert (
        get_checkpoint(
            dynamodb_table, collection_state_key("limitless", "discover", fmt)
        ).last_successful_check
        is not None
    )


def test_malformed_discovery_row_never_advances_page(dynamodb_table, s3_bucket):
    capture, admission, _ = real_inputs("limitless")
    admission = admission.model_copy(update={"enabled": True})
    fmt = next(iter(admission.format_mappings))
    admission = admission.model_copy(
        update={"format_mappings": {fmt: admission.format_mappings[fmt]}}
    )
    client = PagedClient({1: [{"id": 23}]}, capture)
    with pytest.raises(CollectionFailure):
        run(dynamodb_table, s3_bucket, client, admission)
    state = get_checkpoint(dynamodb_table, collection_state_key("limitless", "discover", fmt))
    assert state.page == 0 and state.status == "failed" and state.error == "ValueError"


def test_duplicate_boundary_event_creates_one_work_row(dynamodb_table, s3_bucket):
    capture, admission, _ = real_inputs("limitless")
    fmt = next(iter(admission.format_mappings))
    admission = admission.model_copy(
        update={"enabled": True, "format_mappings": {fmt: admission.format_mappings[fmt]}}
    )
    client = PagedClient(
        {1: [{"id": capture.source_event_id}], 2: [{"id": capture.source_event_id}]},
        capture,
    )
    run(dynamodb_table, s3_bucket, client, admission, page_size=1)
    assert len(event_work_page(dynamodb_table, "limitless", "capture")["Items"]) == 1


def test_bounded_discovery_requests_immediate_continuation(dynamodb_table, s3_bucket):
    capture, admission, _ = real_inputs("limitless")
    fmt = next(iter(admission.format_mappings))
    admission = admission.model_copy(
        update={"enabled": True, "format_mappings": {fmt: admission.format_mappings[fmt]}}
    )
    client = PagedClient({1: [{"id": capture.source_event_id}]}, capture)
    report = run(dynamodb_table, s3_bucket, client, admission, max_pages=1)
    assert report.sources["limitless"].continuation_required


def test_direct_collector_reads_multiple_bounded_pages(s3_bucket):
    capture, admission, _ = real_inputs("limitless")
    fmt = next(iter(admission.format_mappings))
    admission = admission.model_copy(
        update={"enabled": True, "format_mappings": {fmt: admission.format_mappings[fmt]}}
    )
    client = PagedClient({1: [{"id": capture.source_event_id}], 2: [{"id": "second"}]}, capture)
    report = collect_sources(
        admissions=[admission],
        catalog_index=INDEX,
        s3_client=boto3.client("s3"),
        bucket_name=s3_bucket.name,
        clients={"limitless": client},
        page_size=1,
    )
    assert client.calls == [1, 2, 3]
    assert report.sources["limitless"].discovered == 2
