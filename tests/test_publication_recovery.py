from datetime import datetime, timezone

import pytest

from tests.publication_helpers import promote


def test_pointer_crash_retry_and_bounded_enumeration(s3_bucket, dynamodb_table):
    from ingestion.recovery import drain_intents, enumerate_manifest

    m = promote(s3_bucket, dynamodb_table)
    report = drain_intents(table=dynamodb_table, owner="recover", now=100, limit=10)
    assert report.acknowledged == len(m.populations)
    pop = m.populations[0]
    page = enumerate_manifest(table=dynamodb_table, population_id=pop, cursor=None, limit=1)
    assert page.epoch == 2
    assert page.members[0]["revision_id"] == m.revision_id
    assert drain_intents(table=dynamodb_table, owner="recover", now=100, limit=10).acknowledged == 0


@pytest.mark.parametrize("boundary", ["manifest", "ack"])
def test_crash_after_manifest_or_ack_converges(s3_bucket, dynamodb_table, monkeypatch, boundary):
    import ingestion.recovery as recovery

    m = promote(s3_bucket, dynamodb_table)
    target = "reconcile_member" if boundary == "manifest" else "acknowledge_intent"
    original = getattr(recovery, target)

    def crash(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("injected crash")

    monkeypatch.setattr(recovery, target, crash)
    with pytest.raises(RuntimeError):
        recovery.drain_intents(table=dynamodb_table, owner="recover", now=100, limit=10)
    monkeypatch.setattr(recovery, target, original)
    recovery.drain_intents(table=dynamodb_table, owner="recover", now=161, limit=10)
    page = recovery.enumerate_manifest(
        table=dynamodb_table, population_id=m.populations[0], cursor=None, limit=10
    )
    assert len(page.members) == 1 and page.epoch == 2


def test_delayed_superseded_intents_and_reassignment(s3_bucket, dynamodb_table):
    from ingestion.recovery import drain_intents, enumerate_manifest

    old = promote(s3_bucket, dynamodb_table)
    new = promote(
        s3_bucket,
        dynamodb_table,
        mutate=lambda t: (setattr(t.metadata, "rules_id", "M-B"), setattr(t, "teams", [])),
        fetched_at=datetime(2026, 10, 5, tzinfo=timezone.utc),
    )
    # Old work is still pending when the newer event pointer commits.
    report = drain_intents(table=dynamodb_table, owner="recover", now=100, limit=100)
    assert report.acknowledged == 3
    old_page = enumerate_manifest(
        table=dynamodb_table, population_id=old.populations[0], cursor=None, limit=10
    )
    assert old_page.members == []
    new_page = enumerate_manifest(
        table=dynamodb_table, population_id=new.populations[0], cursor=None, limit=10
    )
    assert [m["revision_id"] for m in new_page.members] == [new.revision_id]


def test_expired_competing_population_lease_defers_work(s3_bucket, dynamodb_table):
    from db.leases import acquire_lease
    from db.schema_v2 import lease_key, population_key
    from ingestion.recovery import drain_intents

    m = promote(s3_bucket, dynamodb_table)
    acquire_lease(
        table=dynamodb_table,
        key=lease_key(population_key(m.populations[0])),
        owner="other",
        now=100,
        ttl=10,
    )
    assert drain_intents(table=dynamodb_table, owner="recover", now=100, limit=10).deferred == 1
    assert drain_intents(table=dynamodb_table, owner="recover", now=111, limit=10).acknowledged == 1
