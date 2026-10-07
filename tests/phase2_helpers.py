"""Retained real inputs; test-only staging and synthetic mutation utilities."""

from core.models.publication import RevisionInput
from ingestion.normalize import normalize_capture, normalized_content_hash
from tests.source_helpers import real_inputs


def publish_input(table, bucket, *, second=True, mutate=None, captured_at=None):
    import boto3

    from db.leases import acquire_lease, release_lease
    from db.publication import get, promote_revision
    from db.schema_v2 import event_key, lease_key
    from ingestion.contributions import build_contributions, stage_revision
    from ingestion.revisions import read_staged_result
    from ingestion.staging import stage_capture, stage_normalized

    input, capture, catalog = real_public(second)
    if captured_at:
        capture.fetched_at = captured_at
    _, admission, _ = real_inputs("limitless")
    result = normalize_capture(capture, admission=admission, catalog=catalog)
    if mutate:
        mutate(result.tournament)
    s3 = boto3.client("s3")
    ck = stage_capture(capture, s3_client=s3, bucket_name=bucket.name)
    nk = stage_normalized(result, capture_key=ck, s3_client=s3, bucket_name=bucket.name)
    value = read_staged_result(s3_client=s3, bucket=bucket.name, key=nk, version_id=None)
    m = stage_revision(build_contributions(value), table=table, s3_client=s3, bucket=bucket.name)
    current = get(table, event_key(value.event_id))
    lease = acquire_lease(
        table=table, key=lease_key(event_key(value.event_id)), owner="test", now=100, ttl=60
    )
    promote_revision(
        m,
        table=table,
        lease=lease,
        expected_revision=current["revision_id"] if current else None,
        observation=value,
        now=100,
        clock=lambda: 100,
    )
    release_lease(lease, table=table)
    return value, s3


def real_public(second=False):
    capture, admission, catalog = real_inputs(
        "limitless", fixture="second-accepted-capture.json" if second else "accepted-capture.json"
    )
    result = normalize_capture(capture, admission=admission, catalog=catalog)
    input = RevisionInput(
        result=result,
        bucket="test-bucket",
        normalized_key="staging/v2/normalized/test.json",
        normalized_version_id="test",
        capture_key="staging/v2/captures/test.json",
        capture_version_id="test",
        capture_hash="a" * 64,
        semantic_hash=normalized_content_hash(result.tournament),
        source="limitless",
        event_id=result.tournament.metadata.event_id,
        adapter_version="2.0.0",
        catalog_revision=catalog.snapshot_id,
        captured_at=capture.fetched_at,
        order=int(capture.fetched_at.timestamp() * 1_000_000),
    )
    return input, capture, catalog


def public_bundle(second=False):
    from ingestion.public_contributions import derive_public_event

    input, capture, catalog = real_public(second)
    return derive_public_event(
        input, capture, catalog=catalog, policy_version="limitless-single-bracket-v1"
    )
