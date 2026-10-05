"""Publication exit tests: replay history must equal a clean latest-input rebuild."""

from datetime import datetime, timezone

import boto3
import pytest

from db.publication import get
from db.schema_v2 import population_key
from ingestion.generations import read_generation
from ingestion.native_handler import handle_native_event
from tests.publication_helpers import staged


def event(bucket, key):
    return {"normalized": [{"bucket": bucket.name, "key": key}]}


def totals(table, pop, generation=None):
    generation = generation or get(table, population_key(pop))["publication"]
    return sorted(
        [
            (r["day"] or "", tuple(r["metric"]), r["count"], r["known"], r["unknown"])
            for r in read_generation(table=table, population_id=pop, generation_id=generation)
        ]
    )


def populations(table):
    # Test-only discovery, not a public query or runtime full-table scan.
    rows, cursor = [], None
    while True:
        page = table.scan(**({"ExclusiveStartKey": cursor} if cursor else {}))
        rows.extend(
            r for r in page["Items"] if r["SK"] == "STATE" and r["PK"].startswith("V2#POP#")
        )
        cursor = page.get("LastEvaluatedKey")
        if not cursor:
            return rows


def clean_table():
    return boto3.resource("dynamodb").create_table(
        TableName="clean-rebuild",
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )


def test_real_fixture_replay_and_clean_rebuild(s3_bucket, dynamodb_table, monkeypatch):
    import ingestion.generations as generations

    # Keep the full fixture and real transactions; larger bounded pages reduce Moto copies.
    monkeypatch.setattr(generations, "PAGE_SIZE", 100)
    s3, key = staged(s3_bucket)
    request = event(s3_bucket, key)
    first = handle_native_event(request, table=dynamodb_table, s3_client=s3, now=100, max_steps=500)
    assert first["results"][0]["status"] == "published"
    state = populations(dynamodb_table)[0]
    pop = state["PK"].removeprefix("V2#POP#")
    original = totals(dynamodb_table, pop)
    assert next(r[2] for r in original if r[0] == "" and r[1] == ("rosters",)) == 31
    assert sum(r[4] for r in original if r[0] == "" and r[1][0] == "stat_points") == 186
    handle_native_event(request, table=dynamodb_table, s3_client=s3, now=101, max_steps=500)
    assert totals(dynamodb_table, pop) == original
    fresh = clean_table()
    handle_native_event(request, table=fresh, s3_client=s3, now=100, max_steps=500)
    assert totals(fresh, pop) == original


@pytest.mark.parametrize(
    "boundary",
    ["stage", "promotion", "manifest", "ack", "metric", "complete", "checkpoint", "generation"],
)
def test_crash_at_each_boundary_duplicate_replay_converges(
    s3_bucket, dynamodb_table, monkeypatch, boundary
):
    import ingestion.generations as generations
    import ingestion.native_handler as native
    import ingestion.recovery as recovery

    s3, key = staged(
        s3_bucket, mutate=lambda t: (setattr(t, "teams", t.teams[:1]), setattr(t, "matches", []))
    )
    module, name = {
        "stage": (native, "stage_revision"),
        "promotion": (native, "promote_revision"),
        "manifest": (recovery, "reconcile_member"),
        "ack": (recovery, "acknowledge_intent"),
        "metric": (generations, "_emit"),
        "complete": (generations, "_validate"),
        "checkpoint": (generations, "save_checkpoint"),
        "generation": (native, "publish_generation"),
    }[boundary]
    original = getattr(module, name)

    def crash(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("crash after " + boundary)

    monkeypatch.setattr(module, name, crash)
    with pytest.raises(RuntimeError, match="crash after"):
        native.handle_native_event(
            event(s3_bucket, key), table=dynamodb_table, s3_client=s3, now=100
        )
    monkeypatch.setattr(module, name, original)
    resumed = native.handle_native_event(
        event(s3_bucket, key), table=dynamodb_table, s3_client=s3, now=161
    )
    assert resumed["results"][0]["status"] == "published"
    state = populations(dynamodb_table)[0]
    pop = state["PK"].removeprefix("V2#POP#")
    rows = totals(dynamodb_table, pop)
    assert next(r[2] for r in rows if r[0] == "" and r[1] == ("rosters",)) == 1
    assert state["pending"] == 0


def test_reassignment_after_ack_crash_rebuilds_both_populations(
    s3_bucket, dynamodb_table, monkeypatch
):
    import ingestion.native_handler as native

    s3, key = staged(
        s3_bucket, mutate=lambda t: (setattr(t, "teams", t.teams[:1]), setattr(t, "matches", []))
    )
    native.handle_native_event(event(s3_bucket, key), table=dynamodb_table, s3_client=s3, now=100)
    state = populations(dynamodb_table)[0]
    old_pop = state["PK"].removeprefix("V2#POP#")
    old_generation = state["publication"]
    previous = totals(dynamodb_table, old_pop)
    s3, corrected = staged(
        s3_bucket,
        mutate=lambda t: (
            setattr(t, "teams", []),
            setattr(t, "matches", []),
            setattr(t.metadata, "rules_id", "M-B"),
        ),
        fetched_at=datetime(2026, 10, 5, tzinfo=timezone.utc),
    )
    original = native.drain_intents

    def crash(**kwargs):
        original(**kwargs)
        raise RuntimeError("crash after all acknowledgements")

    monkeypatch.setattr(native, "drain_intents", crash)
    with pytest.raises(RuntimeError):
        native.handle_native_event(
            event(s3_bucket, corrected), table=dynamodb_table, s3_client=s3, now=101
        )
    monkeypatch.setattr(native, "drain_intents", original)
    native.handle_native_event(
        event(s3_bucket, corrected), table=dynamodb_table, s3_client=s3, now=162
    )
    assert totals(dynamodb_table, old_pop) == []
    assert totals(dynamodb_table, old_pop, old_generation) == previous
    fresh = clean_table()
    native.handle_native_event(event(s3_bucket, corrected), table=fresh, s3_client=s3, now=100)
    for row in populations(fresh):
        pop = row["PK"].removeprefix("V2#POP#")
        assert totals(dynamodb_table, pop) == totals(fresh, pop)


def test_periodic_recovery_without_delivery_publishes_dirty_epoch(s3_bucket, dynamodb_table):
    from db.leases import acquire_lease, release_lease
    from db.publication import promote_revision
    from db.schema_v2 import event_key, lease_key
    from ingestion.contributions import build_contributions, stage_revision
    from ingestion.recovery import drain_intents
    from ingestion.revisions import read_staged_result

    s3, key = staged(
        s3_bucket, mutate=lambda t: (setattr(t, "teams", t.teams[:1]), setattr(t, "matches", []))
    )
    value = read_staged_result(s3_client=s3, bucket=s3_bucket.name, key=key, version_id=None)
    m = stage_revision(
        build_contributions(value), table=dynamodb_table, s3_client=s3, bucket=s3_bucket.name
    )
    held = acquire_lease(
        table=dynamodb_table, key=lease_key(event_key(m.event_id)), owner="test", now=100, ttl=60
    )
    promote_revision(m, table=dynamodb_table, lease=held, expected_revision=None, now=100)
    release_lease(held, table=dynamodb_table)
    drain_intents(table=dynamodb_table, owner="old-worker", now=100, limit=100)
    handle_native_event({"recover": True}, table=dynamodb_table, s3_client=s3, now=161)
    assert (
        next(
            r[2]
            for r in totals(dynamodb_table, m.populations[0])
            if r[0] == "" and r[1] == ("rosters",)
        )
        == 1
    )


def test_competing_corrections_removal_and_concurrent_event_match_clean_rebuild(
    s3_bucket, dynamodb_table
):
    def small(t):
        t.teams = t.teams[:1]
        t.matches = []

    s3, first = staged(s3_bucket, mutate=small)
    _, other = staged(s3_bucket, mutate=small, event_id="other-event")
    handle_native_event(event(s3_bucket, first), table=dynamodb_table, s3_client=s3, now=100)
    old_state = populations(dynamodb_table)[0]
    pop = old_state["PK"].removeprefix("V2#POP#")
    old_rows = totals(dynamodb_table, pop)
    handle_native_event(event(s3_bucket, other), table=dynamodb_table, s3_client=s3, now=100)
    assert (
        next(r[2] for r in totals(dynamodb_table, pop) if r[0] == "" and r[1] == ("rosters",)) == 2
    )
    _, conflict = staged(
        s3_bucket, mutate=lambda t: (small(t), setattr(t.metadata, "name", "equal-order conflict"))
    )
    assert (
        handle_native_event(
            event(s3_bucket, conflict), table=dynamodb_table, s3_client=s3, now=100
        )["results"][0]["status"]
        == "pending_order"
    )
    _, corrected = staged(
        s3_bucket,
        mutate=lambda t: (setattr(t, "teams", []), setattr(t, "matches", [])),
        fetched_at=datetime(2026, 10, 5, tzinfo=timezone.utc),
    )
    handle_native_event(event(s3_bucket, corrected), table=dynamodb_table, s3_client=s3, now=101)
    assert (
        handle_native_event(event(s3_bucket, first), table=dynamodb_table, s3_client=s3, now=102)[
            "results"
        ][0]["status"]
        == "stale"
    )
    assert (
        next(r[2] for r in totals(dynamodb_table, pop) if r[0] == "" and r[1] == ("rosters",)) == 1
    )
    assert totals(dynamodb_table, pop, old_state["publication"]) == old_rows
    clean = clean_table()
    handle_native_event(
        {
            "normalized": event(s3_bucket, other)["normalized"]
            + event(s3_bucket, corrected)["normalized"]
        },
        table=clean,
        s3_client=s3,
        now=100,
    )
    assert totals(dynamodb_table, pop) == totals(clean, pop)
