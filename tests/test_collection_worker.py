import json
from datetime import datetime, timedelta, timezone

import boto3
import pytest

from core.models.collection import EventWorkItem
from db.collection_state import event_work_page, get_checkpoint, put_event_work
from db.schema_collection import collection_state_key, event_work_key
from scraper.dispatcher import run_collection_worker, should_refetch
from scraper.handler import lambda_handler
from tests.source_helpers import INDEX, real_inputs

NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)


def work(completed_at=None):
    return EventWorkItem(
        source="limitless", event_id="e", kind="capture", completed_at=completed_at
    )


def test_recent_and_reconcile_windows_include_their_last_day():
    assert should_refetch(work(NOW - timedelta(days=7)), mode="recent", now=NOW)
    assert not should_refetch(work(NOW - timedelta(days=7, seconds=1)), mode="recent", now=NOW)
    assert should_refetch(work(), mode="recent", now=NOW)
    assert should_refetch(work(NOW - timedelta(days=90)), mode="reconcile", now=NOW)
    assert not should_refetch(work(NOW - timedelta(days=90, seconds=1)), mode="reconcile", now=NOW)


def test_worker_requests_continuation_until_all_rows_checked(dynamodb_table, s3_bucket):
    capture, admission, _ = real_inputs("limitless")
    admission = admission.model_copy(update={"enabled": True})
    for number in range(11):
        put_event_work(
            dynamodb_table,
            EventWorkItem(
                source="limitless",
                event_id=f"event-{number}",
                kind="capture",
                status="staged",
                staged_ref={"bucket": s3_bucket.name, "key": "staging/v2/normalized/x.json"},
            ),
        )

    class Invoker:
        calls = 0

        def invoke(self, **kwargs):
            self.calls += 1
            return {"StatusCode": 202}

    invoker = Invoker()
    options = dict(
        admission=admission,
        catalog_index=INDEX,
        s3_client=boto3.client("s3"),
        bucket_name=s3_bucket.name,
        client=None,
        table=dynamodb_table,
        invoke_client=invoker,
        ingestion_function="ingest",
        mode="recent",
        batch_size=10,
        now=NOW,
    )
    assert run_collection_worker(**options).continuation_required
    assert not run_collection_worker(**options).continuation_required
    assert invoker.calls == 11


def test_backfill_requires_explicit_bounded_ids(monkeypatch):
    with pytest.raises(ValueError):
        lambda_handler({"mode": "backfill"})
    with pytest.raises(ValueError):
        lambda_handler({"mode": "backfill", "event_ids": ["x"] * 101})


def test_disabled_reference_mode_does_not_check_sources():
    assert lambda_handler({"mode": "reference"}) == {
        "status": "disabled",
        "reason": "source_admission_not_enabled",
    }


def test_native_lambda_event_keeps_legacy_route(monkeypatch):
    import ingestion.handler as handler

    monkeypatch.setattr(
        "ingestion.native_handler.handle_native_event",
        lambda event, context: {"status": "completed"},
    )
    assert handler.lambda_handler({"mode": "native", "normalized": []}) == {"status": "completed"}


def test_staged_event_handoff_retries_without_recapture(dynamodb_table, s3_bucket):
    capture, admission, _ = real_inputs("limitless")
    admission = admission.model_copy(
        update={"enabled": True, "event_ids": [capture.source_event_id]}
    )

    class Client:
        calls = 0

        def capture_tournament(self, event):
            self.calls += 1
            return capture

    class Invoker:
        calls = 0

        def invoke(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("handoff failed")
            assert kwargs["InvocationType"] == "Event"
            assert b'"mode": "native"' in kwargs["Payload"]
            return {"StatusCode": 202}

    client, invoker = Client(), Invoker()
    options = dict(
        admission=admission,
        catalog_index=INDEX,
        s3_client=boto3.client("s3"),
        bucket_name=s3_bucket.name,
        client=client,
        table=dynamodb_table,
        invoke_client=invoker,
        ingestion_function="ingest",
        mode="backfill",
        backfill_ids=[capture.source_event_id],
    )
    with pytest.raises(Exception):
        run_collection_worker(**options)
    assert client.calls == 1
    state = get_checkpoint(dynamodb_table, collection_state_key("limitless", "backfill", "work#0"))
    assert state.status == "failed" and state.page == 0
    assert (
        event_work_page(dynamodb_table, "limitless", "backfill")["Items"][0]["status"] == "staged"
    )
    run_collection_worker(**options)
    assert client.calls == 1
    assert (
        dynamodb_table.get_item(
            Key=event_work_key("limitless", capture.source_event_id, "backfill")
        )["Item"]["status"]
        == "handed_off"
    )


def test_http_failure_keeps_evidence_and_retries_capture(dynamodb_table, s3_bucket):
    capture, admission, _ = real_inputs("limitless")
    admission = admission.model_copy(update={"enabled": True})
    put_event_work(
        dynamodb_table,
        EventWorkItem(source="limitless", event_id=capture.source_event_id, kind="capture"),
    )

    class Client:
        calls = 0

        def capture_tournament(self, event):
            self.calls += 1
            if self.calls == 1:
                doc = capture.documents["standings"].model_copy(update={"status": 503})
                return capture.model_copy(
                    update={"documents": capture.documents | {"standings": doc}}
                )
            return capture.model_copy(update={"fetched_at": NOW})

    class Invoker:
        def invoke(self, **kwargs):
            return {"StatusCode": 202}

    client = Client()
    options = dict(
        admission=admission,
        catalog_index=INDEX,
        s3_client=boto3.client("s3"),
        bucket_name=s3_bucket.name,
        client=client,
        table=dynamodb_table,
        invoke_client=Invoker(),
        ingestion_function="ingest",
        mode="discover",
    )
    with pytest.raises(Exception):
        run_collection_worker(**options)
    assert client.calls == 1
    assert any(obj.key.startswith("staging/v2/captures/") for obj in s3_bucket.objects.all())
    run_collection_worker(**options)
    assert client.calls == 2


def test_partial_batch_keeps_first_handoff_and_retries_second(dynamodb_table, s3_bucket):
    capture, admission, _ = real_inputs("limitless")
    admission = admission.model_copy(update={"enabled": True})
    first = capture.source_event_id
    second = next(
        f"other-{number}"
        for number in range(100)
        if event_work_key("limitless", f"other-{number}", "backfill")["SK"]
        > event_work_key("limitless", first, "backfill")["SK"]
    )
    ids = [first, second]

    class Client:
        calls = []
        failed = False

        def capture_tournament(self, event):
            self.calls.append(event)
            if event == ids[1] and not self.failed:
                self.failed = True
                raise RuntimeError("temporary source failure")
            return capture.model_copy(update={"source_event_id": event, "fetched_at": NOW})

    class Invoker:
        def invoke(self, **kwargs):
            return {"StatusCode": 202}

    client = Client()
    options = dict(
        admission=admission,
        catalog_index=INDEX,
        s3_client=boto3.client("s3"),
        bucket_name=s3_bucket.name,
        client=client,
        table=dynamodb_table,
        invoke_client=Invoker(),
        ingestion_function="ingest",
        mode="backfill",
        backfill_ids=ids,
        batch_size=2,
    )
    with pytest.raises(Exception):
        run_collection_worker(**options)
    assert (
        dynamodb_table.get_item(Key=event_work_key("limitless", ids[0], "backfill"))["Item"][
            "status"
        ]
        == "handed_off"
    )
    run_collection_worker(**options)
    assert client.calls == [ids[0], ids[1], ids[1]]


@pytest.mark.parametrize(
    ("mode", "ages", "expected"),
    [
        ("recent", {"edge": 7, "old": 8, "unknown": None}, {"edge", "unknown"}),
        ("reconcile", {"edge": 90, "old": 91, "unknown": None}, {"edge"}),
    ],
)
def test_worker_selects_only_due_completion_window(dynamodb_table, s3_bucket, mode, ages, expected):
    capture, admission, _ = real_inputs("limitless")
    admission = admission.model_copy(update={"enabled": True})
    for event_id, age in ages.items():
        put_event_work(
            dynamodb_table,
            EventWorkItem(
                source="limitless",
                event_id=event_id,
                kind="capture",
                completed_at=NOW - timedelta(days=age) if age is not None else None,
            ),
        )

    class Client:
        calls = []

        def capture_tournament(self, event):
            self.calls.append(event)
            return capture.model_copy(update={"source_event_id": event, "fetched_at": NOW})

    class Invoker:
        def invoke(self, **kwargs):
            return {"StatusCode": 202}

    client = Client()
    run_collection_worker(
        admission=admission,
        catalog_index=INDEX,
        s3_client=boto3.client("s3"),
        bucket_name=s3_bucket.name,
        client=client,
        table=dynamodb_table,
        invoke_client=Invoker(),
        ingestion_function="ingest",
        mode=mode,
        now=NOW,
        batch_size=10,
    )
    assert set(client.calls) == expected


def test_handoff_preserves_exact_s3_versions(dynamodb_table, s3_bucket):
    capture, admission, _ = real_inputs("limitless")
    admission = admission.model_copy(update={"enabled": True})
    s3 = boto3.client("s3")
    s3.put_bucket_versioning(Bucket=s3_bucket.name, VersioningConfiguration={"Status": "Enabled"})
    put_event_work(
        dynamodb_table,
        EventWorkItem(source="limitless", event_id=capture.source_event_id, kind="capture"),
    )

    class Client:
        def capture_tournament(self, event):
            return capture

    class Invoker:
        payload = None

        def invoke(self, **kwargs):
            self.payload = json.loads(kwargs["Payload"])
            return {"StatusCode": 202}

    invoker = Invoker()
    run_collection_worker(
        admission=admission,
        catalog_index=INDEX,
        s3_client=s3,
        bucket_name=s3_bucket.name,
        client=Client(),
        table=dynamodb_table,
        invoke_client=invoker,
        ingestion_function="ingest",
        mode="discover",
    )
    ref = invoker.payload["normalized"][0]
    assert ref["version_id"] == s3.head_object(Bucket=s3_bucket.name, Key=ref["key"])["VersionId"]
    staged = json.loads(
        s3.get_object(Bucket=s3_bucket.name, Key=ref["key"], VersionId=ref["version_id"])[
            "Body"
        ].read()
    )
    assert (
        staged["capture_version_id"]
        == s3.head_object(Bucket=s3_bucket.name, Key=staged["capture_key"])["VersionId"]
    )
