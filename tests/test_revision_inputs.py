import json
from datetime import timedelta

import boto3
import pytest

from tests.publication_helpers import revision, staged


def test_exact_version_and_immutable_fallback(s3_bucket):
    from ingestion.revisions import read_staged_result

    s3 = boto3.client("s3")
    s3.put_bucket_versioning(Bucket=s3_bucket.name, VersioningConfiguration={"Status": "Enabled"})
    s3, key = staged(s3_bucket)
    version = s3.head_object(Bucket=s3_bucket.name, Key=key)["VersionId"]
    s3.put_object(Bucket=s3_bucket.name, Key=key, Body=b"{}")
    value = read_staged_result(s3_client=s3, bucket=s3_bucket.name, key=key, version_id=version)
    assert value.normalized_version_id == version
    assert value.event_id.startswith("limitless:")
    with pytest.raises(ValueError):
        read_staged_result(s3_client=s3, bucket=s3_bucket.name, key=key, version_id=None)


def test_content_identity_ignores_fetch_time_but_retains_order(s3_bucket):
    from ingestion.revisions import revision_id

    first = revision(s3_bucket)
    second = revision(s3_bucket, fetched_at=first.captured_at + timedelta(hours=1))
    assert revision_id(first) == revision_id(second)
    assert first.capture_hash != second.capture_hash
    assert first.order < second.order
    conflict = revision(s3_bucket, mutate=lambda t: setattr(t.metadata, "name", "Correction"))
    assert conflict.order == first.order
    assert revision_id(conflict) != revision_id(first)


@pytest.mark.parametrize("change", ["hash", "capture", "pending", "evidence", "event"])
def test_altered_staging_is_rejected(s3_bucket, change):
    from ingestion.revisions import read_staged_result

    s3 = boto3.client("s3")
    s3.put_bucket_versioning(Bucket=s3_bucket.name, VersioningConfiguration={"Status": "Enabled"})
    s3, key = staged(s3_bucket)
    payload = json.loads(s3.get_object(Bucket=s3_bucket.name, Key=key)["Body"].read())
    if change == "hash":
        payload["semantic_hash"] = "0" * 64
    elif change == "capture":
        payload["capture_key"] = "raw/untrusted.json"
    elif change == "pending":
        payload["result"]["admission_status"] = "pending"
    elif change == "event":
        payload["result"]["tournament"]["metadata"]["event_id"] = "limitless:wrong"
    else:
        payload["result"]["tournament"]["metadata"]["evidence"][0]["content_hash"] = "0" * 64
    stored = s3.put_object(
        Bucket=s3_bucket.name, Key="staging/v2/normalized/test.json", Body=json.dumps(payload)
    )
    expected = {
        "hash": "Normalized semantic hash mismatch",
        "capture": "outside the isolated staging namespace",
        "pending": "Only verified results",
        "evidence": "Evidence does not resolve",
        "event": "event identities differ",
    }
    with pytest.raises(ValueError, match=expected[change]):
        read_staged_result(
            s3_client=s3,
            bucket=s3_bucket.name,
            key="staging/v2/normalized/test.json",
            version_id=stored.get("VersionId"),
        )
