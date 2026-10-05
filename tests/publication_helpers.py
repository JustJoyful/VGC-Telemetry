import hashlib
import json

import boto3

from ingestion.normalize import normalize_capture
from ingestion.staging import stage_capture, stage_normalized
from tests.source_helpers import real_inputs


def staged(s3_bucket, mutate=None, fetched_at=None, event_id=None):
    capture, admission, catalog = real_inputs("limitless")
    if fetched_at:
        capture.fetched_at = fetched_at
    if event_id:
        capture.source_event_id = event_id
        for name, doc in capture.documents.items():
            doc.url = f"https://play.limitlesstcg.com/api/tournaments/{event_id}/{name}"
        details = capture.documents["details"]
        value = json.loads(details.body)
        value["id"] = event_id
        details.body = json.dumps(value)
        details.content_hash = hashlib.sha256(details.body.encode()).hexdigest()
    result = normalize_capture(capture, admission=admission, catalog=catalog)
    if mutate:
        mutate(result.tournament)
    s3 = boto3.client("s3")
    ck = stage_capture(capture, s3_client=s3, bucket_name=s3_bucket.name)
    nk = stage_normalized(result, capture_key=ck, s3_client=s3, bucket_name=s3_bucket.name)
    return s3, nk


def revision(s3_bucket, **kwargs):
    from ingestion.revisions import read_staged_result

    s3, key = staged(s3_bucket, **kwargs)
    return read_staged_result(s3_client=s3, bucket=s3_bucket.name, key=key, version_id=None)


def manifest(s3_bucket, table, mutate=None, fetched_at=None, event_id=None):
    from ingestion.contributions import build_contributions, stage_revision

    def small(t):
        t.teams = t.teams[:1]
        t.matches = []
        if mutate:
            mutate(t)

    value = revision(s3_bucket, mutate=small, fetched_at=fetched_at, event_id=event_id)
    return stage_revision(
        build_contributions(value), table=table, s3_client=boto3.client("s3"), bucket=s3_bucket.name
    )


def promote(s3_bucket, table, **kwargs):
    from db.leases import acquire_lease, release_lease
    from db.publication import get, promote_revision
    from db.schema_v2 import event_key, lease_key

    m = manifest(s3_bucket, table, **kwargs)
    key = event_key(m.event_id)
    current = get(table, key)
    held = acquire_lease(table=table, key=lease_key(key), owner="test", now=100, ttl=60)
    promote_revision(
        m,
        table=table,
        lease=held,
        expected_revision=current["revision_id"] if current else None,
        now=100,
    )
    release_lease(held, table=table)
    return m
