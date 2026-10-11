from unittest.mock import Mock


def test_disabled_scheduler_never_calls_sources(monkeypatch):
    from scripts.local.roles import SCHEDULES, scheduler_tick

    monkeypatch.delenv("LOCAL_SCHEDULES_ENABLED", raising=False)
    spy = Mock(side_effect=AssertionError("upstream called"))
    assert scheduler_tick(None, run=spy, now=100) == {"status": "disabled"}
    spy.assert_not_called()
    assert SCHEDULES == {
        "discover": 21600,
        "recent": 21600,
        "reference": 86400,
        "reconcile": 604800,
    }


def test_schedule_registration_is_durable_and_bounded(dynamodb_table, monkeypatch):
    from scripts.local.roles import scheduler_tick

    monkeypatch.setenv("LOCAL_SCHEDULES_ENABLED", "true")
    spy = Mock(return_value={"status": "disabled"})
    assert scheduler_tick(dynamodb_table, run=spy, now=100)["ran"] == 4
    assert scheduler_tick(dynamodb_table, run=spy, now=101)["ran"] == 0
    assert scheduler_tick(dynamodb_table, run=spy, now=21700)["ran"] == 2


def test_expired_scheduler_cannot_commit_completion(dynamodb_table, monkeypatch):
    import pytest
    from botocore.exceptions import ClientError

    from scripts.local.roles import scheduler_tick

    monkeypatch.setenv("LOCAL_SCHEDULES_ENABLED", "true")

    def stolen(mode):
        dynamodb_table.update_item(
            Key={"PK": "V2#LOCAL#SCHEDULE#" + mode, "SK": "LEASE"},
            UpdateExpression="SET expires_at = :past",
            ExpressionAttributeValues={":past": 0},
        )
        return {"status": "completed"}

    with pytest.raises(ClientError):
        scheduler_tick(dynamodb_table, run=stolen, now=100)
    assert "Item" not in dynamodb_table.get_item(Key={"PK": "LOCAL#SCHEDULE", "SK": "discover"})
