from datetime import datetime, timezone

import pytest

from tests.publication_helpers import promote


def finish(table, pop, now=100):
    from ingestion.generations import build_generation

    result = build_generation(
        table=table, population_id=pop, owner="builder", now=now, checkpoint=None
    )
    for _ in range(500):
        if result.complete:
            return result
        result = build_generation(
            table=table, population_id=pop, owner="builder", now=now, checkpoint=result.checkpoint
        )
    raise AssertionError("Build did not converge")


def publish(table, result, expected=None, now=100):
    from db.leases import acquire_lease, release_lease
    from db.schema_v2 import lease_key, population_key
    from ingestion.generations import publish_generation

    held = acquire_lease(
        table=table,
        key=lease_key(population_key(result.population_id)),
        owner="publisher",
        now=now,
        ttl=60,
    )
    try:
        return publish_generation(
            result, table=table, lease=held, expected_publication=expected, now=now
        )
    finally:
        release_lease(held, table=table)


def test_complete_generation_and_old_readability(s3_bucket, dynamodb_table):
    from ingestion.generations import read_generation
    from ingestion.recovery import drain_intents

    first = promote(s3_bucket, dynamodb_table)
    drain_intents(table=dynamodb_table, owner="recovery", now=100, limit=100)
    pop = first.populations[0]
    built = finish(dynamodb_table, pop)
    assert publish(dynamodb_table, built)
    rows = read_generation(
        table=dynamodb_table, population_id=pop, generation_id=built.generation_id
    )
    assert next(r for r in rows if r["metric"] == ["rosters"] and r["day"] is None)["count"] == 1
    promote(
        s3_bucket,
        dynamodb_table,
        mutate=lambda t: setattr(t, "teams", []),
        fetched_at=datetime(2026, 10, 5, tzinfo=timezone.utc),
    )
    drain_intents(table=dynamodb_table, owner="recovery", now=100, limit=100)
    rebuilt = finish(dynamodb_table, pop)
    assert publish(dynamodb_table, rebuilt, built.generation_id)
    assert (
        read_generation(table=dynamodb_table, population_id=pop, generation_id=built.generation_id)
        == rows
    )
    assert (
        read_generation(
            table=dynamodb_table, population_id=pop, generation_id=rebuilt.generation_id
        )
        == []
    )


def test_inputs_changing_before_recovery_reject_stale_publish(s3_bucket, dynamodb_table):
    from ingestion.generations import build_generation
    from ingestion.recovery import drain_intents

    first = promote(s3_bucket, dynamodb_table)
    drain_intents(table=dynamodb_table, owner="recover", now=100, limit=100)
    built = finish(dynamodb_table, first.populations[0])
    promote(
        s3_bucket,
        dynamodb_table,
        mutate=lambda t: setattr(t, "teams", []),
        fetched_at=datetime(2026, 10, 5, tzinfo=timezone.utc),
    )
    assert publish(dynamodb_table, built) is False
    pending = build_generation(
        table=dynamodb_table,
        population_id=first.populations[0],
        owner="builder",
        now=100,
        checkpoint=None,
    )
    assert not pending.complete


def test_interrupted_copy_and_reduce_resume_from_durable_checkpoint(
    s3_bucket, dynamodb_table, monkeypatch
):
    import ingestion.generations as generations
    from ingestion.recovery import drain_intents

    first = promote(s3_bucket, dynamodb_table)
    drain_intents(table=dynamodb_table, owner="recover", now=100, limit=100)
    original = generations.save_checkpoint

    def crash(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("crash after checkpoint")

    monkeypatch.setattr(generations, "save_checkpoint", crash)
    with pytest.raises(RuntimeError):
        generations.build_generation(
            table=dynamodb_table,
            population_id=first.populations[0],
            owner="builder",
            now=100,
            checkpoint=None,
        )
    monkeypatch.setattr(generations, "save_checkpoint", original)
    result = finish(dynamodb_table, first.populations[0], now=161)
    assert result.complete and publish(dynamodb_table, result, now=161)
    rows = generations.read_generation(
        table=dynamodb_table, population_id=first.populations[0], generation_id=result.generation_id
    )
    assert next(r for r in rows if r["metric"] == ["rosters"] and r["day"] is None)["count"] == 1


def test_stale_owner_expected_pointer_and_incomplete_result(s3_bucket, dynamodb_table):
    from db.leases import acquire_lease
    from db.schema_v2 import lease_key, population_key
    from ingestion.generations import build_generation, publish_generation
    from ingestion.recovery import drain_intents

    first = promote(s3_bucket, dynamodb_table)
    drain_intents(table=dynamodb_table, owner="recover", now=100, limit=100)
    partial = build_generation(
        table=dynamodb_table,
        population_id=first.populations[0],
        owner="builder",
        now=100,
        checkpoint=None,
    )
    assert not partial.complete
    assert publish(dynamodb_table, partial) is False
    full = finish(dynamodb_table, first.populations[0])
    assert publish(dynamodb_table, full, "wrong") is False
    held = acquire_lease(
        table=dynamodb_table,
        key=lease_key(population_key(first.populations[0])),
        owner="old",
        now=100,
        ttl=10,
    )
    acquire_lease(table=dynamodb_table, key=held.key, owner="new", now=111, ttl=10)
    assert (
        publish_generation(
            full, table=dynamodb_table, lease=held, expected_publication=None, now=111
        )
        is False
    )


def test_zero_sample_rate_and_bounded_manifest(s3_bucket, dynamodb_table):
    from ingestion.generations import metric_percentage
    from ingestion.recovery import enumerate_manifest

    assert metric_percentage(0, 0) is None
    assert metric_percentage(1, 4) == 25
    m = promote(s3_bucket, dynamodb_table)
    from ingestion.recovery import drain_intents

    drain_intents(table=dynamodb_table, owner="recover", now=100, limit=100)
    page = enumerate_manifest(
        table=dynamodb_table, population_id=m.populations[0], cursor=None, limit=1
    )
    assert len(page.members) == 1


def test_concurrent_events_paginate_and_sum_without_lost_updates(
    s3_bucket, dynamodb_table, monkeypatch
):
    import ingestion.generations as generations
    from ingestion.recovery import drain_intents, enumerate_manifest

    first = promote(s3_bucket, dynamodb_table)
    promote(s3_bucket, dynamodb_table, event_id="synthetic-other-event")
    drain_intents(table=dynamodb_table, owner="recover", now=100, limit=100)
    pop = first.populations[0]
    page = enumerate_manifest(table=dynamodb_table, population_id=pop, cursor=None, limit=1)
    assert page.cursor and len(page.members) == 1
    second = enumerate_manifest(
        table=dynamodb_table, population_id=pop, cursor=page.cursor, limit=1
    )
    assert len(second.members) == 1 and second.members != page.members
    monkeypatch.setattr(generations, "PAGE_SIZE", 7)
    built = finish(dynamodb_table, pop)
    assert publish(dynamodb_table, built)
    rows = generations.read_generation(
        table=dynamodb_table, population_id=pop, generation_id=built.generation_id
    )
    assert next(r for r in rows if r["metric"] == ["rosters"] and r["day"] is None)["count"] == 2
