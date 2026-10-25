from pathlib import Path
from unittest.mock import Mock

import pytest


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


def test_scheduler_beats_before_each_long_running_mode(dynamodb_table, monkeypatch):
    from scripts.local.roles import scheduler_tick

    monkeypatch.setenv("LOCAL_SCHEDULES_ENABLED", "true")
    events = []
    run = Mock(side_effect=lambda mode: events.append(mode) or {"status": "completed"})
    scheduler_tick(dynamodb_table, run=run, now=100, beat=lambda: events.append("beat"))
    assert events == [
        "beat",
        "discover",
        "beat",
        "recent",
        "beat",
        "reference",
        "beat",
        "reconcile",
    ]


@pytest.mark.parametrize(
    ("role", "age", "healthy"),
    [
        ("worker", 170, True),
        ("worker", 200, False),
        ("scheduler", 900, True),
        ("scheduler", 1000, False),
    ],
)
def test_role_health_uses_role_specific_staleness(monkeypatch, tmp_path, role, age, healthy):
    import time

    from scripts.local import cli

    stamp = Path(f"/tmp/vgc-{role}-heartbeat")
    previous = stamp.read_text() if stamp.exists() else None
    monkeypatch.setattr(cli, "validate_local", lambda: None)
    try:
        stamp.write_text(str(time.time() - age))
        if healthy:
            cli.main([f"{role}-health"])
        else:
            with pytest.raises(RuntimeError, match="stale"):
                cli.main([f"{role}-health"])
    finally:
        if previous is None:
            stamp.unlink()
        else:
            stamp.write_text(previous)


def test_stopping_scheduler_mid_collection_releases_its_lease(dynamodb_table, monkeypatch):
    import signal

    from scripts.local import roles

    monkeypatch.setenv("LOCAL_SCHEDULES_ENABLED", "true")
    handlers = {}
    monkeypatch.setattr(roles.signal, "signal", lambda sig, fn: handlers.__setitem__(sig, fn))
    monkeypatch.setattr(roles, "validate_local", lambda: None)
    monkeypatch.setattr(roles, "get_dynamodb_table", lambda: dynamodb_table)
    monkeypatch.setattr(roles, "aws_client", lambda name: None)
    monkeypatch.setattr(roles, "heartbeat", lambda role: None)

    def collecting(mode):
        handlers[signal.SIGTERM](signal.SIGTERM, None)  # docker compose stop, mid-request
        raise AssertionError("collection continued after stop")

    real_tick = roles.scheduler_tick
    monkeypatch.setattr(
        roles, "scheduler_tick", lambda table, beat=None: real_tick(table, run=collecting)
    )
    with pytest.raises(SystemExit):
        roles.serve("scheduler")
    lease = dynamodb_table.get_item(Key={"PK": "V2#LOCAL#SCHEDULE#discover", "SK": "LEASE"})
    assert "Item" not in lease
