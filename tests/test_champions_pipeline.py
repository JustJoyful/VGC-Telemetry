import json

import boto3
import pytest

from core.models.champions import NormalizationResult
from ingestion.normalize import normalize_capture
from ingestion.staging import stage_capture, stage_normalized
from tests.source_helpers import real_inputs
from tests.test_champions_preview import api


def test_real_fixture_to_immutable_readback_preview(s3_bucket):
    c, a, k = real_inputs("limitless")
    s3 = boto3.client("s3")
    result = normalize_capture(c, admission=a, catalog=k)
    ck = stage_capture(c, s3_client=s3, bucket_name=s3_bucket.name)
    nk = stage_normalized(result, capture_key=ck, s3_client=s3, bucket_name=s3_bucket.name)
    payload = json.loads(s3_bucket.Object(nk).get()["Body"].read())
    stored = NormalizationResult.model_validate(payload["result"])
    preview = api().build_preview(stored)
    assert stored == result and preview.tournament == result.tournament
    assert preview.complete_rosters == 31
    assert all(p.stat_points is None for t in preview.tournament.teams for p in t.pokemon)
    assert (
        preview.admission_status == "verified"
        and preview.eligible_rosters == preview.complete_rosters
    )
    assert all(key.key.startswith("staging/v2/") for key in s3_bucket.objects.all())


@pytest.mark.parametrize(
    "key,payload",
    [("staging/v2/normalized/test.json", {}), ("raw/schema2.json", {"schema_version": 2})],
)
def test_legacy_writer_cannot_receive_v2_or_staging(
    key, payload, s3_bucket, dynamodb_table, monkeypatch
):
    from ingestion.handler import handle_s3_event

    written = []
    monkeypatch.setattr("ingestion.handler.write_tournament_data", lambda *a: written.append(a))
    s3_bucket.Object(key).put(Body=json.dumps(payload))
    result = handle_s3_event(
        {"Records": [{"s3": {"bucket": {"name": s3_bucket.name}, "object": {"key": key}}}]}
    )
    assert result["status"] == "skipped" and written == []
