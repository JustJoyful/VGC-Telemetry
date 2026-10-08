"""Actual native handling, explicit recovery and real-fixture HTTP acceptance."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import boto3
import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient

from app.db.public_queries import PublicReader, get_public_reader
from app.main import app
from app.middleware.rate_limit import DynamoDBRateLimitMiddleware
from app.v2.config import V2ReadConfig
from db.publication import get
from db.schema_public import public_state_key
from ingestion.native_handler import handle_native_event
from tests.phase2_helpers import real_public


def stage(bucket, *, second=True, raw_mutate=None, normalized_mutate=None, day=None):
    from ingestion.normalize import normalize_capture
    from ingestion.staging import stage_capture, stage_normalized
    from tests.source_helpers import real_inputs

    _, capture, catalog = real_public(second)
    _, admission, _ = real_inputs("limitless")
    if day:
        capture.fetched_at = datetime(2026, 10, day, tzinfo=UTC)
    if raw_mutate:
        raw_mutate(capture)
        for doc in capture.documents.values():
            doc.content_hash = hashlib.sha256(doc.body.encode()).hexdigest()
    result = normalize_capture(capture, admission=admission, catalog=catalog)
    if normalized_mutate:
        normalized_mutate(result.tournament)
    s3 = boto3.client("s3")
    capture_key = stage_capture(capture, s3_client=s3, bucket_name=bucket.name)
    key = stage_normalized(result, capture_key=capture_key, s3_client=s3, bucket_name=bucket.name)
    return s3, {"normalized": [{"bucket": bucket.name, "key": key}]}


def recover(table, s3):
    for _ in range(100):
        report = handle_native_event(
            {"recover": True}, table=table, s3_client=s3, now=100, max_steps=100
        )
        if report["status"] == "completed" and get(table, public_state_key()).get("snapshot_id"):
            return report
    pytest.fail("Explicit bounded recovery did not converge")


def http(table, monkeypatch):
    monkeypatch.setenv("V2_CURSOR_SECRET", "pipeline-test-only-secret" * 2)
    monkeypatch.setattr(DynamoDBRateLimitMiddleware, "check_rate_limit", lambda *_: (True, 60, 60))
    config = V2ReadConfig(secret=b"pipeline-test-only-secret" * 2, clock=lambda: 100)
    app.dependency_overrides[get_public_reader] = lambda: PublicReader(table, config=config)
    return TestClient(app, raise_server_exceptions=False)


def test_operator_build_uses_existing_operational_retry(dynamodb_table, monkeypatch, capsys):
    import ingestion.public_replay as replay

    original = replay.build_public_snapshot
    attempts = 0

    def throttled(**kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ClientError({"Error": {"Code": "ThrottlingException"}}, "TransactWriteItems")
        return original(**kwargs)

    monkeypatch.setattr(replay, "get_dynamodb_table", lambda: dynamodb_table)
    monkeypatch.setattr(replay, "build_public_snapshot", throttled)
    replay.main(["build", "--max-steps", "1"])
    assert attempts == 2
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "pending" and report["published"] is False


def test_equal_order_evidence_conflict_is_pending_not_completed(dynamodb_table, s3_bucket):
    s3, request = stage(s3_bucket)
    handle_native_event(request, table=dynamodb_table, s3_client=s3, now=100, max_steps=1)

    def conflicting(capture):
        doc = capture.documents["details"]
        value = json.loads(doc.body)
        value["phases"][1]["type"] = "DOUBLE_BRACKET"
        doc.body = json.dumps(value)

    _, conflict = stage(s3_bucket, raw_mutate=conflicting)
    report = handle_native_event(conflict, table=dynamodb_table, s3_client=s3, now=100, max_steps=1)
    assert report["results"][0]["status"] == "pending_order"
    assert report["status"] == "pending"
    assert get(dynamodb_table, public_state_key())["epoch"] == 1


def test_real_native_two_event_analyst_acceptance(dynamodb_table, s3_bucket, monkeypatch):
    from db.schema import evspread_usage_sk, regulation_pk
    from ingestion.public_replay import inventory
    from ingestion.public_snapshot import seed_public_inputs

    table = dynamodb_table
    legacy = {
        "PK": regulation_pk("M-C"),
        "SK": evspread_usage_sk("Swampert"),
        "species": "Swampert",
        "spreads": {"252/252/4/0/0/0": 8},
    }
    table.put_item(Item=legacy)
    client = http(table, monkeypatch)
    try:
        with client:
            legacy_before = client.get("/v1/meta?metric=evspread&species=Swampert").json()
            requests = []
            for second in (False, True):
                s3, request = stage(s3_bucket, second=second)
                requests.append(request)
                handle_native_event(request, table=table, s3_client=s3, now=100, max_steps=100)
            assert get(table, public_state_key())["epoch"] == 2
            handle_native_event(requests[1], table=table, s3_client=s3, now=100, max_steps=100)
            assert get(table, public_state_key())["epoch"] == 2
            ids = inventory(table)["event_ids"]
            assert len(ids) == 2
            assert (
                seed_public_inputs(ids, table=table, s3_client=s3, max_steps=1)["status"]
                == "pending"
            )
            assert (
                seed_public_inputs(ids, table=table, s3_client=s3, max_steps=1)["status"]
                == "complete"
            )
            first = handle_native_event(
                {"recover": True}, table=table, s3_client=s3, now=100, max_steps=1
            )
            assert first["status"] == "pending"
            assert get(table, public_state_key()).get("snapshot_id") is None
            recover(table, s3)
            expected = json.loads(Path("tests/fixtures/phase2/analyst-queries.json").read_text())
            events = client.get("/v2/tournaments?limit=100").json()
            assert events["coverage"]["standings_entries"] == expected["context"]
            assert events["coverage"]["proven_cut_participants"] == expected["cut_participants"]
            assert (
                events["coverage"]["eligible_complete_rosters"]
                == expected["eligible_complete_rosters"]
            )
            assert events["coverage"]["unknown_stat_points"] == expected["unknown_stat_points"]
            assert len(events["items"]) == 2
            teams, cursor, token = [], None, None
            while True:
                params = {"limit": 1, **({"cursor": cursor} if cursor else {})}
                response = client.get("/v2/teams", params=params)
                assert response.status_code == 200, response.text
                page = response.json()
                token = token or page["snapshot"]
                assert page["snapshot"] == token
                teams.extend(page["items"])
                cursor = page["next_cursor"]
                if not cursor:
                    break
            assert sorted(t["team_id"] for t in teams) == sorted(expected["team_ids"])
            assert all(p["stat_points"] is None for t in teams for p in t["pokemon"])
            for case in expected["queries"]:
                response = client.get(case["route"], params={"limit": 100, **case["params"]})
                assert response.status_code == 200, response.text
                row = next(
                    r
                    for r in response.json()["items"]
                    if r["identity"] == case["identity"] and r["value"] == case.get("value")
                )
                assert (row["count"], row["denominator"]) == (case["count"], case["denominator"])
            private = client.get("/v2/meta?metric=stat_points&limit=100").json()
            assert sum(r["unknown_count"] for r in private["items"]) == 60
            assert all(r["percentage"] is None for r in private["items"])
            for event in events["items"]:
                assert client.get("/v2/tournaments/" + event["event_id"]).status_code == 200
                assert (
                    client.get("/v2/tournaments/" + event["event_id"] + "/matches").status_code
                    == 200
                )
                context, context_cursor = [], None
                while True:
                    response = client.get(
                        "/v2/tournaments/" + event["event_id"] + "/standings",
                        params={
                            "limit": 100,
                            "snapshot": events["snapshot"],
                            **({"cursor": context_cursor} if context_cursor else {}),
                        },
                    )
                    assert response.status_code == 200, response.text
                    page = response.json()
                    assert page["snapshot"] == events["snapshot"]
                    context.extend(page["items"])
                    context_cursor = page["next_cursor"]
                    if not context_cursor:
                        break
                assert len(context) == event["coverage"]["standings_entries"]
                assert len({t["team_id"] for t in context}) == len(context)
            assert (
                client.get(
                    "/v2/teams/" + teams[0]["team_id"], params={"snapshot": token}
                ).status_code
                == 200
            )
            assert client.get("/v2/players/limitless:axtheb").status_code == 200
            assert client.get("/v2/regulations").status_code == 200
            assert client.get("/v2/pokemon/floettemega/learnset").status_code == 200
            assert client.get("/v2/status").json()["collection_enabled"] is False
            assert client.get("/v1/meta?metric=evspread&species=Swampert").json() == legacy_before
            assert table.get_item(Key={k: legacy[k] for k in ("PK", "SK")})["Item"] == legacy
    finally:
        app.dependency_overrides.pop(get_public_reader, None)


def test_native_evidence_only_change_and_synthetic_population_correction(
    dynamodb_table, s3_bucket, monkeypatch
):
    from db.schema_v2 import event_key
    from ingestion.public_snapshot import invalidate_public_inputs

    table = dynamodb_table
    s3, original = stage(s3_bucket)
    handle_native_event(original, table=table, s3_client=s3, now=100, max_steps=100)
    recover(table, s3)
    event_id = "limitless:6abe6180880ed327106e0cf4"
    rid = get(table, event_key(event_id))["revision_id"]
    client = http(table, monkeypatch)
    try:
        with client:
            before = client.get("/v2/teams").json()
            assert len(before["items"]) == 2

            def unsupported(capture):
                doc = capture.documents["details"]
                value = json.loads(doc.body)
                value["phases"][1]["type"] = "DOUBLE_BRACKET"  # synthetic contradictory evidence
                doc.body = json.dumps(value)

            _, changed = stage(s3_bucket, raw_mutate=unsupported, day=5)
            handle_native_event(changed, table=table, s3_client=s3, now=100, max_steps=100)
            assert get(table, event_key(event_id))["revision_id"] == rid
            assert get(table, public_state_key())["epoch"] == 2
            recover(table, s3)
            assert client.get("/v2/tournaments/" + event_id).json()["cut"]["status"] == "unknown"
            assert client.get("/v2/meta").json()["items"] == []
            assert (
                len(
                    client.get("/v2/teams", params={"snapshot": before["snapshot"]}).json()["items"]
                )
                == 2
            )

            def remove_and_reassign(tournament):
                # Synthetic normalization dimensions; not a claim about real division evidence.
                tournament.metadata.division_id = "masters"
                team = next(t for t in tournament.teams if t.player_id == "limitless:red5")
                team.publication_status = "absent"
                team.pokemon = []

            _, corrected = stage(s3_bucket, normalized_mutate=remove_and_reassign, day=6)
            handle_native_event(corrected, table=table, s3_client=s3, now=100, max_steps=100)
            recover(table, s3)
            current = client.get("/v2/meta?limit=100").json()
            assert current["coverage"]["eligible_complete_rosters"] == 1
            assert current["coverage"]["absent_rosters"] == 1
            assert all(r["denominator"] == 1 for r in current["items"])
            stable = [
                {k: v for k, v in row.items() if k not in ("snapshot_id", "generated_at")}
                for row in current["items"]
            ]
            invalidate_public_inputs(table=table, policy_version="limitless-single-bracket-v1")
            recover(table, s3)
            rebuilt = client.get("/v2/meta?limit=100").json()["items"]
            assert [
                {k: v for k, v in row.items() if k not in ("snapshot_id", "generated_at")}
                for row in rebuilt
            ] == stable
    finally:
        app.dependency_overrides.pop(get_public_reader, None)
