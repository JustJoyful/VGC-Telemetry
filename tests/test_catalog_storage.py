from datetime import UTC, datetime

import boto3
import pytest
from botocore.exceptions import ClientError

from tests.test_catalog_candidates import candidate, real_input


@pytest.fixture
def store(dynamodb_table):
    from db.catalog import CatalogStore

    s3 = boto3.client("s3")
    s3.create_bucket(Bucket="catalog-test")
    return CatalogStore(table=dynamodb_table, s3_client=s3, bucket="catalog-test")


def test_immutable_storage_cas_effective_time_and_history(store):
    mc = candidate()
    store.save(mc)
    store.save(mc)
    with pytest.raises(ValueError, match="effective"):
        store.activate(mc.candidate_id, expected_epoch=0, now=datetime(2026, 9, 8, tzinfo=UTC))
    first = store.activate(
        mc.candidate_id, expected_epoch=0, now=datetime(2026, 10, 10, tzinfo=UTC)
    )
    assert first["active_revision"] == mc.snapshot.snapshot_id
    assert store.load_revision(mc.snapshot.snapshot_id) == mc.snapshot
    with pytest.raises(ClientError):
        store.activate(mc.candidate_id, expected_epoch=0, now=datetime(2026, 10, 10, tzinfo=UTC))
    # Old catalogs remain pinned; promoting a different revision cannot relabel an event.
    data, docs = real_input("champions-mb-3661ce4")
    mb = candidate(data, docs)
    store.save(mb)
    with pytest.raises(ValueError, match="baseline|effective"):
        store.activate(mb.candidate_id, expected_epoch=1, now=datetime(2026, 10, 10, tzinfo=UTC))
    assert store.load_revision(mc.snapshot.snapshot_id) == mc.snapshot


def test_pending_cannot_activate_and_refresh_keeps_verified(store):
    from core.runtime_catalog import CatalogRuntime

    mc = candidate()
    store.save(mc)
    store.activate(mc.candidate_id, expected_epoch=0, now=datetime(2026, 10, 10, tzinfo=UTC))
    runtime = CatalogRuntime(store)
    assert runtime.refresh().snapshot_id == mc.snapshot.snapshot_id
    data, docs = real_input()
    pending = candidate(data | {"effective_from": None}, docs)
    store.save(pending)
    with pytest.raises(ValueError, match="verified"):
        store.activate(
            pending.candidate_id, expected_epoch=1, now=datetime(2026, 10, 10, tzinfo=UTC)
        )
    original = store.load_revision
    store.load_revision = lambda revision: (_ for _ in ()).throw(OSError("offline"))
    assert runtime.refresh().snapshot_id == mc.snapshot.snapshot_id
    assert runtime.stale
    assert runtime.status()["pending_revision"] == pending.candidate_id
    store.load_revision = original


def test_explicit_rollback_audit_and_retained_history(store):
    mc = candidate()
    data, docs = real_input("champions-mb-3661ce4")
    mb = candidate(data, docs)
    store.save(mc)
    store.save(mb)
    store.activate(mc.candidate_id, expected_epoch=0, now=datetime(2026, 10, 10, tzinfo=UTC))
    with pytest.raises(ValueError):
        store.rollback(mb.candidate_id, expected_epoch=1, operator="", reason="")
    result = store.rollback(mb.candidate_id, expected_epoch=1, operator="operator", reason="review")
    assert result["active_revision"] == mb.snapshot.snapshot_id
    assert store.load_revision(mc.snapshot.snapshot_id) == mc.snapshot
    audit = store.table.get_item(Key={"PK": "V2#CATALOG", "SK": "AUDIT#2"})["Item"]
    assert audit["operator"] == "operator" and audit["reason"] == "review"
    assert audit["action"] == "rollback"


def test_status_projects_catalog_state_without_storage_keys(store):
    from app.db.public_queries import PublicReader
    from app.routers.v2.status import data_status
    from app.v2.config import V2ReadConfig

    mc = candidate()
    store.save(mc)
    store.activate(mc.candidate_id, expected_epoch=0, now=datetime(2026, 10, 10, tzinfo=UTC))
    status = data_status(
        reader=PublicReader(store.table, config=V2ReadConfig()), now=datetime.now(UTC)
    )
    assert status.catalog_active_revision == mc.snapshot.snapshot_id
    assert status.catalog_verification_status == "verified"
    assert "M-A" in status.catalog_coverage[0]
    assert "PK" not in status.model_dump_json()


def test_candidate_tampering_rejected_and_objects_outside_trigger(store):
    mc = candidate()
    corrupt = mc.model_copy(update={"status": "pending"})
    with pytest.raises(ValueError):
        store.save(corrupt)
    store.save(mc)
    objects = store.s3.list_objects_v2(Bucket=store.bucket)["Contents"]
    assert all(o["Key"].startswith("staging/v2/catalog/") for o in objects)


def test_queued_activation_is_conditional_and_effective(store):
    from scraper.reference import activate_ready

    mc = candidate()
    store.save(mc)
    store.queue_activation(mc.candidate_id, expected_epoch=0)
    assert (
        activate_ready(store, documents=mc.documents, now=datetime(2026, 9, 8, tzinfo=UTC))
        == "waiting_effective"
    )
    assert not store.state()
    assert (
        activate_ready(store, documents=mc.documents, now=datetime(2026, 10, 10, tzinfo=UTC))
        == "activated"
    )
    assert store.state()["active_revision"] == mc.snapshot.snapshot_id
    assert (
        activate_ready(store, documents=mc.documents, now=datetime(2026, 10, 10, tzinfo=UTC))
        == "none"
    )


def test_api_refresh_failure_serves_cached_reference_and_reports_stale(store):
    from app.db.public_queries import PublicReader
    from app.routers.v2.status import data_status
    from app.v2.catalog import reference
    from app.v2.config import V2ReadConfig
    from core.runtime_catalog import CatalogRuntime

    mc = candidate()
    store.save(mc)
    store.activate(mc.candidate_id, expected_epoch=0, now=datetime(2026, 10, 10, tzinfo=UTC))
    runtime = CatalogRuntime(store)
    config = V2ReadConfig(catalog_runtime=runtime)
    assert reference(config=config)["default_revision"] == mc.snapshot.snapshot_id
    store.load_revision = lambda revision: (_ for _ in ()).throw(OSError("offline"))
    assert reference(config=config)["default_revision"] == mc.snapshot.snapshot_id
    result = data_status(reader=PublicReader(store.table, config=config), now=datetime.now(UTC))
    assert result.catalog_stale and result.catalog_active_revision == mc.snapshot.snapshot_id


def test_dynamic_reference_pins_runtime_catalog_and_survives_rollback(store):
    from app.models.v2_queries import PokemonQuery
    from app.routers.v2.reference import _request, reference_page
    from app.v2.config import V2ReadConfig
    from core.runtime_catalog import CatalogRuntime

    mc = candidate()
    store.save(mc)
    store.activate(mc.candidate_id, expected_epoch=0, now=datetime(2026, 10, 10, tzinfo=UTC))
    config = V2ReadConfig(
        secret=b"test-secret" * 4, clock=lambda: 100, catalog_runtime=CatalogRuntime(store)
    )
    query = PokemonQuery(limit=1)
    ref, snap, revision, _, position = _request(query, "/v2/pokemon", config)
    assert revision == mc.snapshot.snapshot_id
    first = reference_page(
        catalog_revision=revision,
        reference_manifest_hash=ref["manifest_hash"],
        query=query,
        position=position,
        limit=1,
        config=config,
        snapshot=snap,
    )
    data, docs = real_input("champions-mb-3661ce4")
    mb = candidate(data, docs)
    store.save(mb)
    store.rollback(mb.candidate_id, expected_epoch=1, operator="operator", reason="review")
    pinned_query = PokemonQuery(limit=1, cursor=first.next_cursor)
    old_ref, old_snap, old_revision, _, _ = _request(pinned_query, "/v2/pokemon", config)
    assert old_revision == revision and old_snap == snap
    assert old_ref["catalogs"][revision] == mc.snapshot
