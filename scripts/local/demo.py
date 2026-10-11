"""Retained Limitless evidence through production paths; corrections/faults are synthetic."""

import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path

from core.catalog import load_catalog
from core.catalog_candidates import build_candidate
from core.models.champions import CapturedDocument, SourceCapture
from core.source_admission import load_admissions
from db.catalog import CatalogStore
from db.leases import acquire_lease, release_lease
from db.publication import get
from db.schema_public import public_lease_key, public_state_key, snapshot_pk, snapshot_row_key
from ingestion.contributions import query_rows
from ingestion.native_handler import handle_native_event
from ingestion.normalize import normalize_capture
from ingestion.public_snapshot import (
    build_public_snapshot,
    publish_public_snapshot,
    rollback_public_snapshot,
)
from ingestion.staging import stage_capture, stage_normalized

FIXTURES = Path("tests/fixtures/sources")
INDEX = Path("core/regulations/v2/index.json")


def load_catalogs(table, s3, bucket):
    store = CatalogStore(table=table, s3_client=s3, bucket=bucket)
    documents = {}
    for meta_path in (FIXTURES / "captures").glob("*.json"):
        meta = json.loads(meta_path.read_text())
        body = (meta_path.parent / meta["body_path"]).read_bytes()
        assert hashlib.sha256(body).hexdigest() == meta["sha256"]
        documents[meta["url"]] = CapturedDocument(
            url=meta["url"],
            body=body.decode(),
            content_hash=meta["sha256"],
            content_type=meta["content_type"],
            status=meta["status"],
        )
    revisions = []
    for revision in json.loads(INDEX.read_text())["snapshots"]:
        catalog = load_catalog(revision, index_path=INDEX)
        candidate = build_candidate(
            catalog.model_dump(mode="json"),
            documents={e.url: documents[e.url] for e in catalog.evidence},
        )
        assert candidate.status == "verified", candidate.validation_errors
        store.save(candidate)
        revisions.append({"bundled_revision": revision, "candidate_id": candidate.candidate_id})
    return revisions


def collect(s3, bucket, *, second=False, correction=False):
    path = (
        FIXTURES
        / "limitless"
        / ("second-accepted-capture.json" if second else "accepted-capture.json")
    )
    capture = SourceCapture.model_validate_json(path.read_text())
    admission = next(
        a for a in load_admissions(Path("core/sources/admission.json")) if a.source == "limitless"
    )
    catalog = load_catalog(
        admission.format_mappings[
            json.loads(capture.documents["details"].body)["format"]
        ].catalog_revision,
        index_path=INDEX,
    )
    if correction:
        # Synthetic late correction: remove a public list for a verified cut participant.
        capture.fetched_at = datetime(2026, 10, 6, tzinfo=UTC)
        standings = json.loads(capture.documents["standings"].body)
        next(row for row in standings if row["player"] == "axtheb").pop("decklist", None)
        body = json.dumps(standings, separators=(",", ":"))
        doc = capture.documents["standings"]
        capture.documents["standings"] = CapturedDocument(
            url=doc.url,
            body=body,
            content_type=doc.content_type,
            status=200,
            content_hash=hashlib.sha256(body.encode()).hexdigest(),
        )
        # Original fixture and captured source documents are retained unchanged.
    result = normalize_capture(capture, admission=admission, catalog=catalog)
    assert result.admission_status == "verified", result.diagnostics
    ck = stage_capture(capture, s3_client=s3, bucket_name=bucket)
    cv = s3.head_object(Bucket=bucket, Key=ck)["VersionId"]
    nk = stage_normalized(
        result, capture_key=ck, capture_version_id=cv, s3_client=s3, bucket_name=bucket
    )
    nv = s3.head_object(Bucket=bucket, Key=nk)["VersionId"]
    return {"bucket": bucket, "key": nk, "version_id": nv}, {
        "id": capture.source_event_id,
        "fixture_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "capture_key": ck,
        "synthetic_correction": correction,
    }


def ingest(table, s3, refs):
    return handle_native_event({"normalized": refs}, table=table, s3_client=s3)


def recover(table, s3, *, max_steps=100):
    for _ in range(200):
        report = handle_native_event(
            {"recover": True}, table=table, s3_client=s3, max_steps=max_steps
        )
        if report["status"] == "completed":
            return report
    raise RuntimeError("Local recovery did not converge within 200 bounded calls")


def pointer(table):
    return (get(table, public_state_key()) or {}).get("snapshot_id")


def summary(table):
    sid = pointer(table)
    assert sid and get(table, snapshot_row_key(sid, "META", "COMPLETE"))
    rows = query_rows(table, snapshot_pk(sid, "EVENTS"))["Items"]
    return {
        "rosters": sum(int(r["data"]["coverage"]["standings_entries"]) for r in rows),
        "eligible_rosters": sum(
            int(r["data"]["coverage"]["eligible_complete_rosters"]) for r in rows
        ),
        "unknown_stat_points": sum(int(r["data"]["coverage"]["unknown_stat_points"]) for r in rows),
    }


def publication(table, s3, *, fault=False):
    import ingestion.public_snapshot as snapshots

    def clock():
        return int(time.time())

    lease = acquire_lease(
        table=table, key=public_lease_key(), owner="local-demo-publication", now=clock(), ttl=60
    )
    original = snapshots.immutable_row
    triggered = False

    def interrupted(target, row):
        nonlocal triggered
        original(target, row)
        if row.get("kind") == "team" and not triggered:
            triggered = True
            raise RuntimeError("synthetic-after-first-public-team-write")

    try:
        if fault:
            snapshots.immutable_row = interrupted
        result = build_public_snapshot(table=table, s3_client=s3, lease=lease, clock=clock)
        if fault:
            raise AssertionError("Synthetic fault was not reached")
        assert result.status == "complete"
        assert publish_public_snapshot(result, table=table, lease=lease, clock=clock)
        return result.snapshot_id
    finally:
        snapshots.immutable_row = original
        release_lease(lease, table=table)


def rollback(table, snapshot_id):
    def clock():
        return int(time.time())

    lease = acquire_lease(
        table=table, key=public_lease_key(), owner="local-demo-rollback", now=clock(), ttl=60
    )
    try:
        assert rollback_public_snapshot(
            snapshot_id, table=table, lease=lease, expected_snapshot_id=pointer(table), clock=clock
        )
    finally:
        release_lease(lease, table=table)


def demonstrate(table, s3, bucket, *, api_check=None):
    s3.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
    catalogs = load_catalogs(table, s3, bucket)
    refs, fixtures = [], []
    for second in (False, True):
        ref, evidence = collect(s3, bucket, second=second)
        refs.append(ref)
        fixtures.append(evidence)
    ingest(table, s3, refs)
    recover(table, s3)
    baseline, first = summary(table), pointer(table)
    assert baseline == {"rosters": 37, "eligible_rosters": 10, "unknown_stat_points": 60}
    api = {"baseline": api_check(baseline)} if api_check else {}
    ingest(table, s3, refs)
    recover(table, s3)
    assert summary(table) == baseline and pointer(table) == first
    correction, correction_evidence = collect(s3, bucket, second=True, correction=True)
    ingest(table, s3, [correction])
    try:
        publication(table, s3, fault=True)
    except RuntimeError as error:
        assert str(error) == "synthetic-after-first-public-team-write"
    else:
        raise AssertionError("Expected synthetic interruption")
    assert pointer(table) == first and summary(table) == baseline
    if api_check:
        api["after_fault"] = api_check(baseline)
    recover(table, s3)
    corrected, second = summary(table), pointer(table)
    assert corrected["eligible_rosters"] == 9 and corrected["unknown_stat_points"] == 54
    if api_check:
        api["corrected"] = api_check(corrected)
    rollback(table, first)
    rolled_back = summary(table)
    assert rolled_back == baseline
    if api_check:
        api["rollback"] = api_check(baseline)
    ingest(table, s3, [correction])
    recover(table, s3)
    assert summary(table) == corrected
    if api_check:
        api["replayed"] = api_check(corrected)
    return {
        "api": api,
        "fixtures": fixtures,
        "catalogs": catalogs,
        "baseline": baseline,
        "duplicate_unchanged": True,
        "correction": correction_evidence,
        "corrected": corrected,
        "correction_delta": -1,
        "fault": {
            "name": "synthetic-after-first-public-team-write",
            "current_unchanged": True,
            "before": first,
            "after_fault": first,
            "after_recovery": second,
        },
        "rollback": rolled_back,
        "replayed": summary(table),
        "generations": {"baseline": first, "corrected": second, "replayed": pointer(table)},
    }
