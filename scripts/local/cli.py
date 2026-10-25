"""Local-only operational commands; AWS and upstream admission remain explicit."""

import argparse
import json
import os
import signal
import time
from pathlib import Path
from uuid import uuid4

import httpx

from core.aws import DEMO_NAME, aws_client, validate_local
from db.client import get_dynamodb_table
from db.leases import acquire_lease, release_lease
from scripts.local import demo
from scripts.local.roles import HEARTBEAT_LIMITS, WORKER_LEASE, run_source, serve
from scripts.local.storage import initialize, reset_demo, s3_contract


def api_proof(base_url, expected=None):
    from urllib.parse import urlsplit

    url = urlsplit(base_url)
    if url.scheme != "http" or url.hostname not in {"api", "localhost", "127.0.0.1"}:
        raise ValueError("API proof requires the local FastAPI service")
    paths = [
        "/health",
        "/v1/teams?tournament_id=demo-legacy",
        "/v1/meta",
        "/v1/players/demo",
        "/v2/tournaments?limit=100",
        "/v2/teams?eligibility=all&limit=100",
        "/v2/status",
        "/v2/regulations",
        "/v2/pokemon?limit=100",
        "/v2/meta?metric=species&limit=100",
        "/v2/meta?metric=stat_points&limit=100",
        "/v2/cores?size=3&limit=100",
    ]
    outcomes = []
    with httpx.Client(base_url=base_url, trust_env=False, timeout=60) as client:
        for path in paths:
            response = client.get(path)
            assert response.status_code == 200, (path, response.status_code, response.text[:300])
            body = response.json()
            outcomes.append(
                {
                    "path": path,
                    "status": response.status_code,
                    "rows": len(body.get("items", body.get("data", [])))
                    if isinstance(body.get("items", body.get("data")), list)
                    else None,
                    "bytes": len(response.content),
                }
            )
        if expected:
            events = client.get("/v2/tournaments?limit=100").json()["items"]
            coverage = {
                "rosters": sum(e["coverage"]["standings_entries"] for e in events),
                "eligible_rosters": sum(e["coverage"]["eligible_complete_rosters"] for e in events),
                "unknown_stat_points": sum(e["coverage"]["unknown_stat_points"] for e in events),
            }
            assert coverage == expected, coverage
        teams = client.get("/v2/teams?eligibility=all&limit=100").json()["items"]
        if teams:
            known = next((t for t in teams if t.get("pokemon")), teams[0])
            detail = client.get("/v2/teams/" + known["team_id"])
            assert detail.status_code == 200
            assert detail.json()["provenance"]
            assert all(p["stat_points"] is None for p in detail.json()["pokemon"])
            outcomes.append(
                {
                    "path": "/v2/teams/<id>",
                    "status": 200,
                    "bytes": len(detail.content),
                    "provenance_checked": True,
                }
            )
    return outcomes


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=[
            "init",
            "storage-contract",
            "reset-demo",
            "catalogs",
            "collect",
            "normalize",
            "ingest",
            "publish",
            "recover",
            "replay",
            "rollback",
            "demo",
            "seed-demo",
            "api-proof",
            "worker",
            "scheduler",
            "worker-health",
            "scheduler-health",
            "reference",
            "discover",
            "recent",
            "reconcile",
            "refresh",
        ],
    )
    parser.add_argument("--confirm-reset", action="store_true")
    parser.add_argument("--refs", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--correction", action="store_true")
    parser.add_argument("--synthetic-fault", action="store_true")
    parser.add_argument("--snapshot-id")
    parser.add_argument("--api-url", default="http://api:8080")
    args = parser.parse_args(argv)
    validate_local()
    if args.command.endswith("-health"):
        role = args.command.split("-")[0]
        stamp = float(Path(f"/tmp/vgc-{role}-heartbeat").read_text())
        if time.time() - stamp > HEARTBEAT_LIMITS[role]:
            raise RuntimeError("Local role heartbeat stale")
        return
    if args.command in {"worker", "scheduler"}:
        import logging
        import sys

        # Both roles log redacted telemetry; the scheduler also logs request URLs and
        # rate-limit waits. Neither logs upstream bodies.
        logging.basicConfig(
            stream=sys.stderr,
            level=logging.INFO if args.command == "scheduler" else logging.WARNING,
            format="%(asctime)s %(name)s %(message)s",
        )
        serve(args.command)
        return
    if args.command == "init":
        report = initialize()
    elif args.command == "reset-demo":
        report = reset_demo(confirm=args.confirm_reset)
    elif args.command == "storage-contract":
        report = s3_contract(aws_client("s3"), DEMO_NAME)
    elif args.command == "api-proof":
        report = api_proof(args.api_url)
    elif args.command in {"reference", "discover", "recent", "reconcile"}:
        report = run_source(args.command)
    elif args.command == "refresh":
        import logging
        import sys

        from scripts.local.live import refresh

        # Progress (request URLs, rate-limit waits, redacted outcomes) goes to stderr only.
        logging.basicConfig(
            stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(name)s %(message)s"
        )

        def stopped(*_):
            # `docker stop` must unwind so finally blocks release the schedule leases.
            raise SystemExit(143)

        signal.signal(signal.SIGTERM, stopped)
        # Holds every schedule lease for the whole bounded refresh.
        report = refresh(
            get_dynamodb_table(),
            aws_client("s3"),
            DEMO_NAME,
            admission_path=Path(
                os.environ.get("SOURCE_ADMISSION_PATH", "core/sources/admission.json")
            ),
            catalog_index=Path(
                os.environ.get("CATALOG_INDEX_PATH", "core/regulations/v2/index.json")
            ),
        )
    else:
        if args.command == "demo":
            reset_demo(confirm=args.confirm_reset)
        table, s3 = get_dynamodb_table(), aws_client("s3")
        lease = acquire_lease(
            table=table, key=WORKER_LEASE, owner=uuid4().hex, now=int(time.time()), ttl=900
        )
        try:
            if args.command == "seed-demo":
                if os.environ.get("LOCAL_SCHEDULES_ENABLED", "false") != "false":
                    raise ValueError("seed-demo requires disabled live schedules")
                report = demo.seed(
                    table,
                    s3,
                    DEMO_NAME,
                    api_check=lambda expected: api_proof(args.api_url, expected),
                )
            elif args.command == "demo":
                # Reset needs quiescent roles; run the documented command with worker stopped.
                report = demo.demonstrate(
                    table,
                    s3,
                    DEMO_NAME,
                    api_check=lambda expected: api_proof(args.api_url, expected),
                )
            elif args.command == "catalogs":
                report = demo.load_catalogs(table, s3, DEMO_NAME)
            elif args.command in {"collect", "normalize"}:
                report = [
                    demo.collect(s3, DEMO_NAME, second=s, correction=args.correction and s)[0]
                    for s in (False, True)
                ]
            elif args.command == "ingest":
                if args.refs is None:
                    parser.error("ingest requires --refs")
                report = demo.ingest(table, s3, json.loads(args.refs.read_text()))
            elif args.command in {"recover", "replay"}:
                report = demo.recover(table, s3)
            elif args.command == "publish":
                report = {"snapshot_id": demo.publication(table, s3, fault=args.synthetic_fault)}
            elif args.command == "rollback":
                if not args.snapshot_id:
                    parser.error("rollback requires --snapshot-id")
                demo.rollback(table, args.snapshot_id)
                report = {"snapshot_id": args.snapshot_id}
        finally:
            # A reset drops the old lease; only release leases still retained.
            from db.publication import get

            if get(table, WORKER_LEASE):
                release_lease(lease, table=table)
    rendered = json.dumps(report, sort_keys=True)
    if args.output:
        with args.output.open("x") as stream:
            stream.write(rendered + "\n")
    print(rendered)
    if args.command == "refresh" and report["status"] in {"failed", "disabled"}:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
