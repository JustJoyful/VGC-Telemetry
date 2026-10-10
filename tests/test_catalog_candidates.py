import copy
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from core.catalog import load_catalog
from core.models.champions import CapturedDocument


def real_input(revision="champions-mc-3661ce4"):
    snapshot = load_catalog(revision, index_path=Path("core/regulations/v2/index.json"))
    documents = {}
    for evidence in snapshot.evidence:
        for path in Path("tests/fixtures/sources/captures").glob("*.json"):
            meta = json.loads(path.read_text())
            if meta["url"] == evidence.url:
                documents[evidence.url] = CapturedDocument(
                    url=evidence.url,
                    body=(path.parent / meta["body_path"]).read_text(),
                    content_type=meta["content_type"],
                    content_hash=meta["sha256"],
                    status=meta["status"],
                )
                break
    return snapshot.model_dump(mode="json"), documents


def candidate(data=None, documents=None, **kwargs):
    from core.catalog_candidates import build_candidate

    if data is None:
        data, documents = real_input()
    return build_candidate(data, documents=documents, **kwargs)


def test_real_candidates_content_identity_coverage_and_unchanged():
    for revision in ("champions-mb-3661ce4", "champions-mc-3661ce4"):
        data, documents = real_input(revision)
        first = candidate(data, documents)
        assert first.status == "verified", first.validation_errors
        assert first == candidate(dict(reversed(list(data.items()))), documents)
        assert first.snapshot.snapshot_id.startswith("champions-")
        assert first.snapshot.snapshot_id != revision
        assert "M-A" in first.historical_coverage
        assert len(first.registration_ids) == (261 if "mc" in revision else 235)
        assert candidate(
            data,
            documents,
            previous=load_catalog(revision, index_path=Path("core/regulations/v2/index.json")),
        ).unchanged


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"coverage_complete": False}, "incomplete_coverage"),
        ({"effective_from": None}, "effective_time_conflict"),
        ({"effective_from": "2026-09-10T02:00:00Z"}, "effective_time_conflict"),
        ({"species": {}}, "model_validation"),
        ({"species": None}, "model_validation"),
        ({"evidence": ["malformed"]}, "model_validation"),
        ({"source_kind": "official"}, "supplementary_authority_required"),
        ({"upstream_revision": "unknown"}, "upstream_evidence_missing"),
        ({"natures": {"timid": {"name": "Timid"}}}, "bootstrap_export_unverified"),
    ],
)
def test_invalid_candidates_fail_closed(change, reason):
    data, documents = real_input()
    old = copy.deepcopy(data)
    result = candidate(data | change, documents)
    assert result.status == "pending"
    assert reason in result.validation_errors
    assert data == old


def test_reviewed_changes_need_exact_deltas_and_retained_official_quotations():
    data, docs = real_input()
    old = load_catalog("champions-mb-3661ce4", index_path=Path("core/regulations/v2/index.json"))
    pending = candidate(data, docs, previous=old)
    official = next(ref for ref in data["evidence"] if "/page/" in ref["url"])
    review = {
        "reviewer": "synthetic-reviewer",
        "reason": "Fixture review only",
        "evidence": [official],
        "quotations": {official["url"]: "Mega Baxcalibur"},
        "additions": pending.additions,
        "removals": pending.removals,
    }
    assert candidate(data, docs, previous=old, review=review).status == "verified"
    review["removals"] = {}
    assert (
        "official_change_conflict"
        in candidate(data, docs, previous=old, review=review).validation_errors
    )


def test_missing_and_mismatched_evidence_are_pending():
    data, documents = real_input()
    assert "evidence_missing" in candidate(data, {}).validation_errors
    data["evidence"][0]["content_hash"] = "0" * 64
    assert "evidence_hash_mismatch" in candidate(data, documents).validation_errors


def test_additions_removals_need_reviewed_official_evidence():
    data, documents = real_input()
    previous = load_catalog(
        "champions-mb-3661ce4", index_path=Path("core/regulations/v2/index.json")
    )
    result = candidate(data, documents, previous=previous)
    assert result.additions["species"] == sorted(set(data["species"]) - set(previous.species))
    assert result.removals["items"] == sorted(set(previous.items) - set(data["items"]))
    assert "change_review_required" in result.validation_errors
    assert previous.snapshot_id == "champions-mb-3661ce4"


def test_reference_check_preserves_evidence_and_flags_changes(dynamodb_table):
    from core.telemetry import RunTelemetry
    from scraper.reference import check_references

    class Response:
        status_code = 200
        headers = {"content-type": "text/plain"}

        def __init__(self, url, body):
            self.url, self.content = url, body

    class Client:
        changed = False

        def get(self, url, **kwargs):
            return Response(url, b"changed" if self.changed else b"same")

    class S3:
        bodies = {}

        def put_object(self, **kwargs):
            self.bodies[kwargs["Key"]] = kwargs["Body"]

    now = datetime(2026, 10, 10, tzinfo=UTC)
    client, s3 = Client(), S3()

    class Metrics:
        def put_metric_data(self, **kwargs):
            pass

    telemetry = RunTelemetry("reference", source="official", client=Metrics())
    first = check_references(
        table=dynamodb_table,
        s3_client=s3,
        bucket="raw",
        client=client,
        now=now,
        telemetry=telemetry,
    )
    assert first["status"] == "pending"  # newly observed sources require resolution/review
    assert telemetry.counts["CatalogPending"] == 1
    assert first["build_acquisition"] == "disabled"
    second = check_references(
        table=dynamodb_table, s3_client=s3, bucket="raw", client=client, now=now
    )
    assert second["changed"] == []
    assert all(key.startswith("staging/v2/catalog/") for key in s3.bodies)
    assert any(hashlib.sha256(b"same").hexdigest() in key for key in s3.bodies)
    client.changed = True
    assert check_references(
        table=dynamodb_table, s3_client=s3, bucket="raw", client=client, now=now
    )["changed"]


def test_reference_worker_rechecks_and_activates_queued_export(dynamodb_table):
    import boto3

    from db.catalog import CatalogStore
    from scraper.reference import check_references

    data, docs = real_input()
    _, prior_docs = real_input("champions-mb-3661ce4")
    all_docs = prior_docs | docs

    class Client:
        def get(self, url, **kwargs):
            doc = all_docs.get(url)
            return type(
                "Response",
                (),
                {
                    "url": url,
                    "status_code": 200,
                    "headers": {"content-type": doc.content_type if doc else "application/json"},
                    "content": doc.body.encode() if doc else b"{}",
                },
            )()

    s3 = boto3.client("s3")
    s3.create_bucket(Bucket="queued-catalog-test")
    store = CatalogStore(table=dynamodb_table, s3_client=s3, bucket="queued-catalog-test")
    proposed = candidate(data, docs)
    store.save(proposed)
    store.queue_activation(proposed.candidate_id, expected_epoch=0)
    result = check_references(
        table=dynamodb_table,
        s3_client=s3,
        bucket=store.bucket,
        client=Client(),
        now=datetime(2026, 10, 10, tzinfo=UTC),
    )
    assert result["activation"] == "activated"
    assert store.state()["active_revision"] == proposed.snapshot.snapshot_id
