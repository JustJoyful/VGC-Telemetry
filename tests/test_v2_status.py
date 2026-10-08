# ruff: noqa: F811
from datetime import UTC, datetime


def test_unpublished_valid_capture_is_visible_without_fabricated_check(dynamodb_table):
    from app.db.public_queries import PublicReader
    from app.routers.v2.status import data_status
    from app.v2.config import V2ReadConfig
    from db.public_inputs import note_public_capture

    newer = int(datetime(2026, 10, 5, tzinfo=UTC).timestamp() * 1_000_000)
    note_public_capture(newer, table=dynamodb_table)
    note_public_capture(newer - 86400_000_000, table=dynamodb_table)
    result = data_status(
        reader=PublicReader(dynamodb_table, config=V2ReadConfig(secret=b"secret" * 8)),
        now=datetime(2026, 10, 6, tzinfo=UTC),
    )
    assert result.last_capture == datetime(2026, 10, 5, tzinfo=UTC)
    assert result.published_at is None and result.snapshot_id is None
    assert result.last_successful_check is None


def test_status_is_manual_and_reports_no_invented_check_time(dynamodb_table):
    from app.db.public_queries import PublicReader
    from app.routers.v2.status import data_status
    from app.v2.config import V2ReadConfig

    reader = PublicReader(dynamodb_table, config=V2ReadConfig(secret=b"secret" * 8))
    status = data_status(reader=reader, now=datetime(2026, 10, 6, tzinfo=UTC))
    assert status.mode == "manual" and status.collection_enabled is False
    assert status.last_successful_check is None and status.source_errors is None
    assert status.snapshot_id is None and status.last_capture is None
    assert status.published_at is None and status.stale is None


def test_status_capture_publication_and_dirty_epoch_are_distinct(v2_client, monkeypatch):
    from app.db.public_queries import PublicReader
    from app.routers.v2.status import data_status
    from app.v2.config import V2ReadConfig
    from db.schema_public import public_state_key

    client, _, table, _ = v2_client
    table.update_item(
        Key=public_state_key(),
        UpdateExpression="SET dirty = :yes ADD epoch :one",
        ExpressionAttributeValues={":yes": True, ":one": 1},
    )

    def forbidden(**kwargs):
        raise AssertionError("Status must not scan")

    monkeypatch.setattr(table, "scan", forbidden)
    status = data_status(
        reader=PublicReader(table, config=V2ReadConfig(secret=b"secret" * 8)),
        now=datetime(2026, 10, 6, tzinfo=UTC),
    )
    assert status.dirty and status.input_epoch == 2
    assert status.last_capture.year == 2026 and status.published_at.year == 1970
    assert status.stale is True
    assert status.coverage.eligible_complete_rosters == 2
    assert status.last_successful_check is None
    assert client.get("/v2/status").status_code == 200
    assert client.get("/v2/status?unknown_filter=ignored").status_code == 422
    assert client.get("/health").status_code == 200


from tests.v2_helpers import v2_client  # noqa: E402, F401
