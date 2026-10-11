"""Explicit operator migration/recovery. Collection remains disabled; retain all data."""

import argparse
import json
import time
from pathlib import Path
from uuid import uuid4

from core.aws import aws_client
from db.client import get_dynamodb_table
from db.leases import acquire_lease, release_lease
from db.schema_public import public_lease_key
from ingestion.public_snapshot import (
    build_public_snapshot,
    invalidate_public_inputs,
    publish_public_snapshot,
    rollback_public_snapshot,
    seed_public_inputs,
)
from ingestion.retry import retry


def inventory(table) -> dict:
    """Operator-only paginated scan. Export IDs for explicit seed comparison."""
    event_ids, cursor = [], None
    while True:
        args = {"Limit": 100, "ProjectionExpression": "PK, SK, event_id"}
        if cursor:
            args["ExclusiveStartKey"] = cursor
        page = table.scan(**args)
        event_ids.extend(
            row["event_id"]
            for row in page["Items"]
            if row["PK"].startswith("V2#EVENT#") and row["SK"] == "STATE"
        )
        cursor = page.get("LastEvaluatedKey")
        if not cursor:
            break
    return {"event_ids": sorted(set(event_ids)), "count": len(set(event_ids))}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="mode", required=True)
    inv = commands.add_parser("inventory", help="Read-only operator scan; export IDs separately")
    inv.add_argument("--events-file", type=Path, required=True)
    seed = commands.add_parser("seed")
    seed.add_argument("--events-file", type=Path, required=True)
    seed.add_argument("--max-steps", type=int, default=100)
    for mode in ("build", "recover"):
        commands.add_parser(mode).add_argument("--max-steps", type=int, default=100)
    commands.add_parser("invalidate").add_argument("--policy-version", required=True)
    rollback = commands.add_parser("rollback")
    rollback.add_argument("--snapshot-id", required=True)
    rollback.add_argument("--expected-snapshot-id", required=True)
    args = parser.parse_args(argv)
    table, s3 = get_dynamodb_table(), aws_client("s3")

    def clock():
        return int(time.time())

    if args.mode == "inventory":
        report = retry(lambda: inventory(table))
        args.events_file.write_text(json.dumps(report.pop("event_ids")) + "\n")
    elif args.mode == "seed":
        event_ids = json.loads(args.events_file.read_text())
        report = retry(
            lambda: seed_public_inputs(
                event_ids, table=table, s3_client=s3, max_steps=args.max_steps
            )
        )
    elif args.mode == "invalidate":
        retry(lambda: invalidate_public_inputs(table=table, policy_version=args.policy_version))
        report = {"status": "dirty", "policy_version": args.policy_version}
    elif args.mode == "recover":
        from ingestion.native_handler import handle_native_event

        report = handle_native_event(
            {"recover": True}, table=table, s3_client=s3, max_steps=args.max_steps
        )
    else:
        owner = uuid4().hex
        lease = retry(
            lambda: acquire_lease(
                table=table, key=public_lease_key(), owner=owner, now=clock(), ttl=60
            )
        )
        try:
            if args.mode == "rollback":
                report = {
                    "published": retry(
                        lambda: rollback_public_snapshot(
                            args.snapshot_id,
                            table=table,
                            lease=lease,
                            expected_snapshot_id=args.expected_snapshot_id,
                            clock=clock,
                        )
                    ),
                    "snapshot_id": args.snapshot_id,
                }
            else:
                result = retry(
                    lambda: build_public_snapshot(
                        table=table,
                        s3_client=s3,
                        lease=lease,
                        clock=clock,
                        max_steps=args.max_steps,
                    )
                )
                report = {
                    "status": result.status,
                    "snapshot_id": result.snapshot_id,
                    "phase": result.checkpoint.phase,
                    "published": result.status == "complete"
                    and retry(
                        lambda: publish_public_snapshot(
                            result, table=table, lease=lease, clock=clock
                        )
                    ),
                }
        finally:
            retry(lambda: release_lease(lease, table=table))
    print(json.dumps(report))


if __name__ == "__main__":
    main()
