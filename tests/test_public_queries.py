import pytest


def test_two_real_events_pin_pages_across_correction(dynamodb_table, s3_bucket):
    from datetime import UTC, datetime

    from app.db.public_queries import PublicReader
    from app.models.v2_queries import TournamentQuery
    from app.v2.config import V2ReadConfig
    from tests.phase2_helpers import publish_input
    from tests.test_public_snapshot import finish, held

    first_input, s3 = publish_input(dynamodb_table, s3_bucket, second=False)
    second_input, _ = publish_input(dynamodb_table, s3_bucket, second=True)
    lease = held(dynamodb_table)
    finish(dynamodb_table, s3, lease)
    reader = PublicReader(
        dynamodb_table, config=V2ReadConfig(secret=b"secret" * 8, clock=lambda: 100)
    )
    query = TournamentQuery(limit=1)
    snapshot, position = reader.request_snapshot(query, "/v2/tournaments")
    page = reader.page(snapshot, section="EVENTS", query=query, position=position, limit=1)
    assert page.next_cursor
    publish_input(
        dynamodb_table,
        s3_bucket,
        mutate=lambda t: setattr(t, "teams", []),
        captured_at=datetime(2026, 10, 5, tzinfo=UTC),
    )
    finish(dynamodb_table, s3, lease)
    query.cursor = page.next_cursor
    pinned, position = reader.request_snapshot(query, "/v2/tournaments")
    last = reader.page(pinned, section="EVENTS", query=query, position=position, limit=1)
    assert last.snapshot == page.snapshot
    assert last.next_cursor is None
    assert {page.items[0].event_id, last.items[0].event_id} == {
        first_input.event_id,
        second_input.event_id,
    }
    assert reader.get_event(pinned, second_input.event_id).coverage.eligible_complete_rosters == 2
    assert (
        reader.get_event(
            reader.resolve_snapshot(None), second_input.event_id
        ).coverage.eligible_complete_rosters
        == 0
    )


def test_filtered_empty_page_advances(dynamodb_table):
    from app.db.public_queries import PublicReader
    from app.models.v2_queries import TeamQuery
    from app.v2.config import V2ReadConfig
    from db.schema_public import public_state_key, snapshot_row_key

    table = dynamodb_table
    table.put_item(Item={**public_state_key(), "snapshot_id": "synthetic", "epoch": 1})
    table.put_item(
        Item={
            **snapshot_row_key("synthetic", "META", "COMPLETE"),
            "schema_version": "public-read-v1",
            "policy_version": "limitless-single-bracket-v1",
        }
    )
    with table.batch_writer() as batch:
        for index in range(2001):
            batch.put_item(
                Item={
                    **snapshot_row_key("synthetic", "TEAMS", f"{index:05}"),
                    "data": dict(
                        team_id=f"limitless:e:p{index}",
                        event_id="limitless:e",
                        publication_status="absent",
                        eligibility={"status": "included", "reason": "synthetic"},
                        pokemon=[{"species_id": "wanted" if index == 2000 else "other"}],
                    ),
                }
            )
    reader = PublicReader(table, config=V2ReadConfig(secret=b"test-secret" * 4, clock=lambda: 100))
    original_query = table.query
    calls = []

    def observed(**kwargs):
        calls.append(kwargs)
        assert kwargs["ConsistentRead"] is True
        return original_query(**kwargs)

    table.query = observed
    snap = reader.resolve_snapshot(None)
    query = TeamQuery(species_id="wanted")
    first = reader.page(snap, section="TEAMS", query=query, position=None, limit=25)
    assert first.items == []
    assert first.next_cursor
    assert len(calls) == 20
    assert sum(call["Limit"] for call in calls) == 2000
    from app.models.v2_queries import canonical_query
    from app.v2.cursors import decode_cursor

    cursor = decode_cursor(
        first.next_cursor,
        endpoint="/v2/teams",
        query=canonical_query(query),
        limit=25,
        now=100,
        secret=reader.config.secret,
    )
    assert cursor["position"] == "01999"
    last = reader.page(snap, section="TEAMS", query=query, position=cursor["position"], limit=25)
    assert len(last.items) == 1
    assert last.next_cursor is None


def test_storage_failure_is_safe_503(dynamodb_table, monkeypatch):
    from botocore.exceptions import ClientError

    from app.db.public_queries import PublicReader
    from app.v2.config import V2ReadConfig
    from app.v2.errors import V2Error

    def failed(**kwargs):
        raise ClientError({"Error": {"Code": "InternalServerError"}}, "GetItem")

    monkeypatch.setattr(dynamodb_table, "get_item", failed)
    with pytest.raises(V2Error) as failure:
        PublicReader(dynamodb_table, config=V2ReadConfig(secret=b"secret" * 8)).get({})
    assert failure.value.status == 503


@pytest.mark.parametrize(
    "values,expected",
    [
        ({"division_id": "masters"}, False),
        ({"competition_id": "different"}, False),
        ({"catalog_revision": "historic"}, True),
        ({"date_from": "2026-01-01"}, False),
        ({"placement_max": 10}, False),
        ({"eligibility": "all"}, True),
        ({"player_id": "limitless:other"}, False),
    ],
)
def test_unknown_dimensions_and_colliding_names_never_match(dynamodb_table, values, expected):
    from app.db.public_queries import PublicReader
    from app.models.v2_queries import TeamQuery
    from app.v2.config import V2ReadConfig

    reader = PublicReader(dynamodb_table, config=V2ReadConfig(secret=b"secret" * 8))
    data = {
        "player_name": "Same display name",
        "player_id": "limitless:one",
        "catalog_revision": "historic",
        "division_id": None,
        "competition_id": None,
        "standing": None,
        "starts_at": None,
        "eligibility": {"status": "included"},
    }
    assert reader._matches(data, TeamQuery(**values)) is expected


def test_size_limit_never_drops_an_oversized_item(dynamodb_table):
    from app.db.public_queries import PublicReader
    from app.models.v2 import SnapshotInfo
    from app.models.v2_queries import TeamQuery
    from app.v2.config import V2ReadConfig
    from app.v2.errors import V2Error
    from db.schema_public import snapshot_row_key

    dynamodb_table.put_item(
        Item={
            **snapshot_row_key("synthetic", "TEAMS", "1"),
            "data": {
                "team_id": "limitless:e:p",
                "event_id": "limitless:e",
                "publication_status": "absent",
                "player_name": "x" * 10000,
                "eligibility": {"status": "included", "reason": "synthetic"},
            },
        }
    )
    reader = PublicReader(
        dynamodb_table, config=V2ReadConfig(secret=b"secret" * 8, response_bytes=8000)
    )
    with pytest.raises(V2Error) as failure:
        reader.page(
            SnapshotInfo(snapshot_id="synthetic", expires_at=999),
            section="TEAMS",
            query=TeamQuery(),
            position=None,
            limit=25,
        )
    assert failure.value.status == 503


def test_absent_snapshot_is_unavailable(dynamodb_table):
    from app.db.public_queries import PublicReader
    from app.v2.config import V2ReadConfig
    from app.v2.errors import V2Error

    with pytest.raises(V2Error) as failure:
        PublicReader(
            dynamodb_table, config=V2ReadConfig(secret=b"test-secret" * 4)
        ).resolve_snapshot(None)
    assert failure.value.status == 503
