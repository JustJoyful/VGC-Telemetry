import importlib
import importlib.util
import json
from datetime import timedelta

import boto3

from core.models.champions import NormalizationResult
from ingestion.normalize import normalize_capture
from tests.source_helpers import real_inputs


def api():
    assert importlib.util.find_spec("ingestion.staging"), "Isolated staging is missing"
    return importlib.import_module("ingestion.staging")


def test_immutable_capture_and_normalized_readback(s3_bucket):
    c, a, k = real_inputs("limitless")
    s3 = boto3.client("s3")
    module = api()
    key = module.stage_capture(c, s3_client=s3, bucket_name=s3_bucket.name)
    assert key.startswith("staging/v2/captures/") and not key.startswith("raw/")
    assert module.stage_capture(c, s3_client=s3, bucket_name=s3_bucket.name) == key
    assert json.loads(s3_bucket.Object(key).get()["Body"].read()) == c.model_dump(mode="json")
    result = normalize_capture(c, admission=a, catalog=k)
    nkey = module.stage_normalized(
        result, capture_key=key, s3_client=s3, bucket_name=s3_bucket.name
    )
    payload = json.loads(s3_bucket.Object(nkey).get()["Body"].read())
    assert payload["schema_version"] == 2 and payload["capture_key"] == key
    assert NormalizationResult.model_validate(payload["result"]) == result
    assert payload["semantic_hash"]
    assert (
        module.stage_capture(
            c.model_copy(update={"fetched_at": c.fetched_at + timedelta(seconds=1)}),
            s3_client=s3,
            bucket_name=s3_bucket.name,
        )
        != key
    )


def test_pending_capture_stages_diagnostics_without_guessing_catalog(s3_bucket):
    s3 = boto3.client("s3")
    c, _, _ = real_inputs("limitless")
    key = api().stage_capture(c, s3_client=s3, bucket_name=s3_bucket.name)
    result = NormalizationResult(
        admission_status="pending",
        diagnostics=[{"code": "rules_unknown", "path": "metadata", "message": "Needs evidence"}],
    )
    normalized = api().stage_normalized(
        result, capture_key=key, s3_client=s3, bucket_name=s3_bucket.name
    )
    payload = json.loads(s3_bucket.Object(normalized).get()["Body"].read())
    assert payload["semantic_hash"] is None and payload["result"]["admission_status"] == "pending"


def test_staging_rejects_raw_capture_reference(s3_bucket):
    import pytest

    with pytest.raises(ValueError):
        api().stage_normalized(
            NormalizationResult(admission_status="pending"),
            capture_key="raw/event.json",
            s3_client=boto3.client("s3"),
            bucket_name=s3_bucket.name,
        )
