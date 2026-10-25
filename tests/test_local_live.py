import hashlib
import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
import yaml

from core.models.champions import SourceCapture
from scripts.local.live import refresh, snapshot_id
from tests.source_helpers import INDEX

FIXTURES = Path("tests/fixtures/sources/limitless")


def capture(name):
    value = SourceCapture.model_validate_json((FIXTURES / name).read_text())
    return value.model_copy(update={"fetched_at": datetime.now(UTC)})


def listing(value):
    details = json.loads(value.documents["details"].body)
    return {"id": value.source_event_id, "date": details["date"], "format": details["format"]}


class FakeLimitless:
    """Recorded public captures only; CI never contacts the upstream source."""

    def __init__(self, captures=(), fail=None):
        self.captures = {c.source_event_id: c for c in captures}
        self.fail = fail
        self.requests = []
        self.client = SimpleNamespace(close=lambda: None)

    def __call__(self, on_retry=None):
        return self

    def get_tournaments(self, game, format, limit, page):
        self.requests.append(("list", format, page))
        if self.fail == "discovery":
            raise httpx.ConnectError("source unavailable")
        rows = [listing(c) for c in self.captures.values() if listing(c)["format"] == format]
        return rows if page == 1 else []

    def capture_tournament(self, event_id):
        self.requests.append(("capture", event_id))
        if self.fail == "capture":
            raise httpx.HTTPStatusError(
                "429 Too Many Requests",
                request=httpx.Request("GET", "https://example.invalid"),
                response=httpx.Response(429),
            )
        return self.captures[event_id].model_copy(update={"fetched_at": datetime.now(UTC)})


def admission_file(tmp_path, *, enabled=True, verified=True):
    bundled = json.loads(Path("core/sources/admission.json").read_text())
    for source in bundled["sources"]:
        source["enabled"] = enabled
        source["verified"] = verified
    path = tmp_path / "admission.json"
    path.write_text(json.dumps(bundled))
    return path


def run(table, bucket, path, client, **options):
    return refresh(
        table,
        bucket.meta.client,
        bucket.name,
        admission_path=path,
        catalog_index=INDEX,
        client_factory=client,
        **options,
    )


def public_events(table):
    from db.schema_public import snapshot_pk
    from ingestion.contributions import query_rows

    return query_rows(table, snapshot_pk(snapshot_id(table), "EVENTS"))["Items"]


@pytest.fixture
def versioned(s3_bucket):
    s3_bucket.meta.client.put_bucket_versioning(
        Bucket=s3_bucket.name, VersioningConfiguration={"Status": "Enabled"}
    )
    return s3_bucket


def test_bundled_offline_admission_never_contacts_upstream(dynamodb_table, versioned):
    client = Mock(side_effect=AssertionError("upstream client constructed"))
    report = run(dynamodb_table, versioned, Path("core/sources/admission.json"), client)
    assert report["status"] == "disabled"
    assert report["reason"] == "source_admission_not_enabled"
    client.assert_not_called()
    assert snapshot_id(dynamodb_table) is None


@pytest.mark.parametrize("problem", ["unverified", "malformed", "missing"])
def test_live_collection_requires_valid_explicit_admission(
    dynamodb_table, versioned, tmp_path, problem
):
    if problem == "unverified":
        path = admission_file(tmp_path, verified=False)
    elif problem == "malformed":
        path = tmp_path / "admission.json"
        path.write_text('{"sources": [{"source": "limitless", "enabled": "yes please"}]}')
    else:
        path = tmp_path / "absent.json"
    client = Mock(side_effect=AssertionError("upstream client constructed"))
    report = run(dynamodb_table, versioned, path, client)
    assert report["status"] == "failed"
    assert report["reason"] == (
        "admission_unverified" if problem == "unverified" else "admission_invalid"
    )
    client.assert_not_called()


def test_refresh_publishes_newest_events_and_repeats_without_duplicates(
    dynamodb_table, versioned, tmp_path
):
    from scripts.local.demo import summary

    client = FakeLimitless(
        [capture("accepted-capture.json"), capture("second-accepted-capture.json")]
    )
    path = admission_file(tmp_path)
    first = run(dynamodb_table, versioned, path, client)
    assert first["status"] == "completed", first
    assert first["sources"]["limitless"]["accepted"] == 2
    assert first["snapshot_changed"] and first["publication"] == "published"
    baseline, events = summary(dynamodb_table), public_events(dynamodb_table)
    assert baseline["rosters"] > 0 and len(events) == 2
    second = run(dynamodb_table, versioned, path, client)
    assert second["status"] == "completed", second
    assert summary(dynamodb_table) == baseline
    assert len(public_events(dynamodb_table)) == len(events)


def test_refresh_captures_only_the_bounded_newest_events(dynamodb_table, versioned, tmp_path):
    client = FakeLimitless(
        [capture("accepted-capture.json"), capture("second-accepted-capture.json")]
    )
    report = run(dynamodb_table, versioned, admission_file(tmp_path), client, newest=1)
    # The 13:00 event is newer than the 12:00 event; the other remains queued.
    assert [r for r in client.requests if r[0] == "capture"] == [
        ("capture", "6ab9204c880ed327106dddf4")
    ]
    assert report["status"] == "pending"
    assert report["sources"]["limitless"]["remaining"] == 1


@pytest.mark.parametrize("fail", ["discovery", "capture"])
def test_upstream_failure_preserves_previous_snapshot(dynamodb_table, versioned, tmp_path, fail):
    from scripts.local.demo import summary

    path = admission_file(tmp_path)
    captures = [capture("accepted-capture.json")]
    assert run(dynamodb_table, versioned, path, FakeLimitless(captures))["status"] == "completed"
    before, rows = snapshot_id(dynamodb_table), summary(dynamodb_table)
    report = run(dynamodb_table, versioned, path, FakeLimitless(captures, fail=fail))
    assert report["status"] == "failed"
    assert report["sources"]["limitless"]["failed"] >= 1
    assert snapshot_id(dynamodb_table) == before and summary(dynamodb_table) == rows
    assert report["snapshot_changed"] is False and report["publication"] == "deferred"


def test_unadmitted_platform_reports_no_eligible_events(dynamodb_table, versioned, tmp_path):
    value = capture("accepted-capture.json")
    details = json.loads(value.documents["details"].body)
    details["platform"] = "SIM"
    body = json.dumps(details)
    # A consistent recorded document: its hash must match, or staging rejects it.
    document = value.documents["details"].model_copy(
        update={"body": body, "content_hash": hashlib.sha256(body.encode()).hexdigest()}
    )
    documents = {**value.documents, "details": document}
    client = FakeLimitless([value.model_copy(update={"documents": documents})])
    report = run(dynamodb_table, versioned, admission_file(tmp_path), client)
    assert report["status"] == "no_eligible_events"
    assert report["sources"]["limitless"]["rejected"] == 1
    assert report["sources"]["limitless"]["diagnostics"] == {"platform_conflict": 1}
    assert snapshot_id(dynamodb_table) is None


def test_empty_source_reports_no_eligible_events(dynamodb_table, versioned, tmp_path):
    report = run(dynamodb_table, versioned, admission_file(tmp_path), FakeLimitless())
    assert report["status"] == "no_eligible_events"
    assert report["sources"]["limitless"]["discovered"] == 0


def test_refresh_refuses_to_race_an_active_collector(dynamodb_table, versioned, tmp_path):
    from db.leases import acquire_lease

    acquire_lease(
        table=dynamodb_table,
        key={"PK": "V2#LOCAL#SCHEDULE#recent", "SK": "LEASE"},
        owner="scheduler",
        now=int(datetime.now(UTC).timestamp()),
        ttl=900,
    )
    client = Mock(side_effect=AssertionError("second collector started"))
    report = run(dynamodb_table, versioned, admission_file(tmp_path), client)
    assert report["status"] == "failed" and report["reason"] == "collection_in_progress"
    client.assert_not_called()
    for mode in ("discover", "reference", "reconcile"):
        key = {"PK": "V2#LOCAL#SCHEDULE#" + mode, "SK": "LEASE"}
        assert "Item" not in dynamodb_table.get_item(Key=key)


def test_leftover_worker_lease_does_not_block_refresh(dynamodb_table, versioned, tmp_path):
    from db.leases import acquire_lease
    from scripts.local.roles import WORKER_LEASE

    # A recovery worker stopped mid-call can leave its lease until expiry.
    acquire_lease(
        table=dynamodb_table,
        key=WORKER_LEASE,
        owner="stopped-worker",
        now=int(datetime.now(UTC).timestamp()),
        ttl=900,
    )
    report = run(dynamodb_table, versioned, admission_file(tmp_path), FakeLimitless())
    assert report["status"] == "no_eligible_events"


def test_rate_limit_pacer_waits_for_advertised_reset():
    from scraper.limitless_client import RateLimitPacer

    waits = []
    pacer = RateLimitPacer(sleep=waits.append, clock=lambda: 0.0)
    pacer.observe(httpx.Response(200, headers={"ratelimit": '"50-in-5min"; r=40; t=200'}))
    pacer.wait()
    assert waits == []
    pacer.observe(httpx.Response(200, headers={"ratelimit": '"50-in-5min"; r=1; t=275'}))
    pacer.wait()
    assert waits == [276.0]
    pacer.observe(httpx.Response(429, headers={"ratelimit": '"50-in-5min"; r=0; t=9999'}))
    pacer.wait()
    assert waits[-1] == 331.0


def test_limitless_client_paces_every_request():
    from scraper.limitless_client import LimitlessAPIClient, RateLimitPacer

    waits = []
    pacer = RateLimitPacer(sleep=waits.append, clock=lambda: 0.0)

    def handler(request):
        return httpx.Response(200, json=[], headers={"ratelimit": '"50-in-5min"; r=0; t=10'})

    client = LimitlessAPIClient(
        client=httpx.Client(transport=httpx.MockTransport(handler)), pacer=pacer
    )
    client.get_tournaments(format="M-C")
    client.get_tournaments(format="M-C")
    assert waits == [11.0]


# Host orchestration: Docker is replaced with a recorder; no containers start.


def record(monkeypatch, *, running="", report=None, fail=False):
    from scripts.local import stack

    calls = []
    output = json.dumps(report or {"status": "completed", "message": "ok", "sources": {}})

    def command(*args, capture=False, files=("compose.yaml",)):
        calls.append((args, files))
        if "ps" in args:
            return running
        if "run" in args:
            if fail:
                raise subprocess.CalledProcessError(1, args, output=output)
            return "progress\n" + output
        return None

    monkeypatch.setattr(stack, "command", command)
    monkeypatch.setattr(stack, "ensure_live_admission", lambda: False)
    return calls


def actions(calls):
    skipped = {"-d", "--wait", "--wait-timeout", "180"}
    return [tuple(a for a in args[4:] if a not in skipped) for args, _ in calls]


def test_live_startup_finishes_refresh_before_scheduled_collection(monkeypatch):
    from scripts.local import stack

    calls = record(monkeypatch)
    stack.main(["live"])
    assert actions(calls) == [
        ("up", "--build", "api"),
        ("stop", "worker", "scheduler"),
        ("run", "--rm", "-T", "collector"),
        ("up", "worker", "scheduler"),
    ]
    assert all(files == stack.LIVE_FILES for _, files in calls)


def test_live_startup_restores_roles_after_failed_refresh(monkeypatch):
    from scripts.local import stack

    calls = record(monkeypatch, report={"status": "failed", "message": "x"}, fail=True)
    with pytest.raises(SystemExit):
        stack.main(["live"])
    assert actions(calls)[-1] == ("up", "worker", "scheduler")


@pytest.mark.parametrize("fail", [False, True])
def test_manual_refresh_from_running_stack_restores_roles(monkeypatch, fail):
    from scripts.local import stack

    running = "api\ndynamodb\nstorage\nworker\nscheduler\n"
    report = {"status": "failed" if fail else "pending", "message": "x"}
    calls = record(monkeypatch, running=running, report=report, fail=fail)
    if fail:
        with pytest.raises(SystemExit):
            stack.main(["refresh"])
    else:
        stack.main(["refresh"])
    assert actions(calls) == [
        ("ps", "--status", "running", "--services"),
        ("stop", "worker", "scheduler"),
        ("run", "--rm", "-T", "collector"),
        ("start", "worker", "scheduler"),
    ]


def test_manual_refresh_from_stopped_stack_leaves_it_stopped(monkeypatch):
    from scripts.local import stack

    calls = record(monkeypatch, running="")
    stack.main(["refresh", "--events", "25"])
    steps = actions(calls)
    assert ("run", "--rm", "-T", "-e", "LIVE_REFRESH_EVENTS=25", "collector") in steps
    assert steps[-1] == ("stop", "dynamodb", "storage")
    assert not any(step[0] == "start" for step in steps)


def test_manual_refresh_restores_roles_when_docker_itself_fails(monkeypatch):
    from scripts.local import stack

    calls = []

    def command(*args, capture=False, files=("compose.yaml",)):
        calls.append(args)
        if "ps" in args:
            return "worker\nscheduler\napi\n"
        if "run" in args:
            raise KeyboardInterrupt
        return None

    monkeypatch.setattr(stack, "command", command)
    monkeypatch.setattr(stack, "ensure_live_admission", lambda: False)
    with pytest.raises(KeyboardInterrupt):
        stack.main(["refresh"])
    assert calls[-1][-3:] == ("start", "worker", "scheduler")


def test_generated_admission_is_explicit_local_and_gitignored(tmp_path, monkeypatch):
    from scripts.local import stack

    target = tmp_path / ".local/admission.json"
    (tmp_path / "core/sources").mkdir(parents=True)
    shutil.copy("core/sources/admission.json", tmp_path / "core/sources/admission.json")
    monkeypatch.setattr(stack, "LIVE_ADMISSION", target)
    monkeypatch.setattr(stack, "ROOT", tmp_path)
    assert stack.ensure_live_admission() is True
    sources = json.loads(target.read_text())["sources"]
    bundled = json.loads(Path("core/sources/admission.json").read_text())["sources"]
    assert [s["enabled"] for s in sources] == [True]
    # Coverage is copied, never expanded.
    assert [{**s, "enabled": False} for s in sources] == bundled
    assert stack.ensure_live_admission() is False
    ignored = subprocess.run(["git", "check-ignore", "-q", ".local/admission.json"])
    assert ignored.returncode == 0


def test_live_overlay_keeps_ingress_closed_and_offline_default():
    config = yaml.safe_load(Path("compose.yaml").read_text())
    live = yaml.safe_load(Path("compose.live.yaml").read_text())
    assert set(live["services"]) == {"scheduler", "collector"}
    for service in live["services"].values():
        assert not service.get("ports")
        assert "edge" not in service.get("networks", [])
    assert live["services"]["collector"]["profiles"] == ["collector"]
    # The default stack gives collection roles no egress and keeps schedules disabled.
    assert "egress" not in config["services"]["scheduler"].get("networks", [])
    assert "LOCAL_SCHEDULES_ENABLED=false" in Path("local/compose.env.example").read_text()
    assert config["services"]["tunnel"]["profiles"] == ["tunnel"]
    assert config["services"]["admin"]["profiles"] == ["admin"]
    assert config["services"]["admin"]["ports"][0].startswith("127.0.0.1:")
