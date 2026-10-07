from datetime import datetime

import boto3
import pytest
from botocore.exceptions import ClientError

from db.leases import acquire_lease
from db.publication import get, promote_revision
from db.schema_v2 import event_key, lease_key
from tests.publication_helpers import manifest


def promotion(table, m, now=100):
    key = event_key(m.event_id)
    held = acquire_lease(table=table, key=lease_key(key), owner="test", now=now, ttl=60)
    current = get(table, key)
    return held, dict(
        table=table,
        lease=held,
        expected_revision=current["revision_id"] if current else None,
        now=now,
    )


def test_revision_and_public_dirty_work_are_atomic(dynamodb_table, s3_bucket, monkeypatch):
    from db.schema_public import public_member_key, public_state_key

    table = dynamodb_table
    m = manifest(s3_bucket, table)
    held, kwargs = promotion(table, m)
    original = table.meta.client.transact_write_items

    def fail(**args):
        if len(args["TransactItems"]) > 1:
            raise RuntimeError("injected transaction failure")
        return original(**args)

    monkeypatch.setattr(table.meta.client, "transact_write_items", fail)
    with pytest.raises(RuntimeError):
        promote_revision(m, **kwargs)
    assert get(table, event_key(m.event_id)) is None
    assert get(table, public_member_key(m.event_id)) is None
    monkeypatch.setattr(table.meta.client, "transact_write_items", original)
    assert promote_revision(m, **kwargs).status == "promoted"
    assert get(table, public_member_key(m.event_id))["revision_id"] == m.revision_id
    assert get(table, public_state_key())["epoch"] == 1
    assert get(table, public_state_key())["dirty"] is True


def test_new_phase_evidence_same_normalized_content_marks_dirty(dynamodb_table, s3_bucket):
    from db.public_inputs import PendingPublicOrder, public_input, public_registration_actions
    from db.schema_public import public_member_key, public_state_key
    from ingestion.revisions import read_staged_result

    table = dynamodb_table
    first = manifest(s3_bucket, table)
    _, kwargs = promotion(table, first)
    promote_revision(first, **kwargs)
    kwargs["expected_revision"] = first.revision_id
    second = manifest(s3_bucket, table, fetched_at=datetime.fromisoformat("2026-10-04T19:08:52Z"))
    assert first.revision_id == second.revision_id
    from db.schema_v2 import revision_pk

    obs = get(
        table, {"PK": revision_pk(second.event_id, second.revision_id), "SK": f"OBS#{second.order}"}
    )["input"]
    observed = read_staged_result(
        s3_client=boto3.client("s3"),
        bucket=s3_bucket.name,
        key=obs["normalized_key"],
        version_id=None,
    )
    promote_revision(second, **kwargs, observation=observed)
    member = get(table, public_member_key(first.event_id))
    assert member["order"] == second.order
    assert get(table, public_state_key())["epoch"] == 2
    promote_revision(first, **kwargs)
    promote_revision(second, **kwargs)
    assert get(table, public_state_key())["epoch"] == 2
    value = public_input(observed)
    with pytest.raises(PendingPublicOrder):
        public_registration_actions(
            value.model_copy(update={"capture_hash": "a" * 64}),
            current_pointer=get(table, event_key(first.event_id)),
            current_member=member,
        )


def test_public_directory_rejects_expired_publisher(dynamodb_table, s3_bucket):
    from db.schema_public import public_member_key

    m = manifest(s3_bucket, dynamodb_table)
    _, kwargs = promotion(dynamodb_table, m)
    with pytest.raises(ClientError):
        promote_revision(m, **kwargs, clock=lambda: 161)
    assert get(dynamodb_table, public_member_key(m.event_id)) is None


def test_atomic_public_registration_capacity_and_distinct_actions(
    dynamodb_table, s3_bucket, monkeypatch
):
    from db.schema_v2 import revision_pk

    table = dynamodb_table
    m = manifest(s3_bucket, table)
    m.populations = [f"pop{i:02}" for i in range(40)]
    table.put_item(
        Item={
            "PK": revision_pk(m.event_id, m.revision_id),
            "SK": "MANIFEST",
            **m.model_dump(mode="json"),
        }
    )
    _, kwargs = promotion(table, m)
    calls = []
    original = table.meta.client.transact_write_items

    def observe(**args):
        calls.append(args["TransactItems"])
        return original(**args)

    monkeypatch.setattr(table.meta.client, "transact_write_items", observe)
    promote_revision(m, **kwargs)
    assert len(calls[1]) == 84
    keys = [
        (v.get("Key") or {k: v["Item"][k] for k in ("PK", "SK")})
        for a in calls[1]
        for v in a.values()
    ]
    assert len({(k["PK"], k["SK"]) for k in keys}) == 84
    m.populations.append("pop40")
    table.put_item(
        Item={
            "PK": revision_pk(m.event_id, m.revision_id),
            "SK": "MANIFEST",
            **m.model_dump(mode="json"),
        }
    )
    kwargs["expected_revision"] = m.revision_id
    with pytest.raises(ValueError, match="Too many"):
        promote_revision(m, **kwargs)


def test_raw_phase_change_same_content_pins_validated_input(dynamodb_table, s3_bucket):
    import hashlib
    import json
    from datetime import UTC, datetime

    from db.schema_public import public_member_key, public_state_key
    from ingestion.contributions import build_contributions, stage_revision
    from ingestion.normalize import normalize_capture
    from ingestion.revisions import read_staged_result
    from ingestion.staging import stage_capture, stage_normalized
    from tests.source_helpers import real_inputs

    table, s3 = dynamodb_table, boto3.client("s3")
    observations = []
    capture, admission, catalog = real_inputs("limitless")
    for day in (4, 5):
        capture.fetched_at = datetime(2026, 10, day, tzinfo=UTC)
        details = json.loads(capture.documents["details"].body)
        details["phases"][1]["rounds"] = day
        capture.documents["details"].body = json.dumps(details)
        capture.documents["details"].content_hash = hashlib.sha256(
            capture.documents["details"].body.encode()
        ).hexdigest()
        result = normalize_capture(capture, admission=admission, catalog=catalog)
        result.tournament.teams = result.tournament.teams[:1]
        result.tournament.matches = []
        ck = stage_capture(capture, s3_client=s3, bucket_name=s3_bucket.name)
        nk = stage_normalized(result, capture_key=ck, s3_client=s3, bucket_name=s3_bucket.name)
        value = read_staged_result(s3_client=s3, bucket=s3_bucket.name, key=nk, version_id=None)
        m = stage_revision(
            build_contributions(value), table=table, s3_client=s3, bucket=s3_bucket.name
        )
        if not observations:
            _, kwargs = promotion(table, m)
        else:
            kwargs["expected_revision"] = m.revision_id
        promote_revision(m, **kwargs, observation=value)
        observations.append(value)
    assert observations[0].semantic_hash == observations[1].semantic_hash
    assert observations[0].capture_hash != observations[1].capture_hash
    assert get(table, public_state_key())["epoch"] == 2
    assert (
        get(table, public_member_key(m.event_id))["normalized_key"]
        == observations[1].normalized_key
    )
    promote_revision(
        m, **kwargs, observation=observations[1].model_copy(update={"capture_hash": "a" * 64})
    )
    assert get(table, public_state_key())["epoch"] == 2
