"""Explicit evidence submission, guarded activation and audited rollback; no source enablement."""

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import boto3

from core.catalog_candidates import build_candidate
from core.models.champions import CapturedDocument
from db.catalog import CatalogStore
from db.client import get_dynamodb_table


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    submit = sub.add_parser("submit")
    submit.add_argument("bundle", type=Path, help="JSON snapshot/documents/review bundle")
    for action in ("activate", "rollback"):
        command = sub.add_parser(action)
        command.add_argument("candidate_id")
        command.add_argument("--expected-epoch", type=int, required=True)
        if action == "rollback":
            command.add_argument("--operator", required=True)
            command.add_argument("--reason", required=True)
    queue = sub.add_parser("queue")
    queue.add_argument("candidate_id")
    queue.add_argument("--expected-epoch", type=int, required=True)
    args = parser.parse_args()
    store = CatalogStore(
        table=get_dynamodb_table(),
        s3_client=boto3.client("s3"),
        bucket=os.environ["S3_RAW_BUCKET_NAME"],
    )
    if args.action == "submit":
        bundle = json.loads(args.bundle.read_text())
        active = store.state().get("active_revision")
        candidate = build_candidate(
            bundle["snapshot"],
            documents={
                url: CapturedDocument.model_validate(doc)
                for url, doc in bundle["documents"].items()
            },
            previous=store.load_revision(active) if active else None,
            review=bundle.get("review"),
        )
        store.save(candidate)
        result = {
            "candidate_id": candidate.candidate_id,
            "status": candidate.status,
            "validation_errors": candidate.validation_errors,
        }
    elif args.action == "queue":
        store.queue_activation(args.candidate_id, expected_epoch=args.expected_epoch)
        result = {"candidate_id": args.candidate_id, "status": "queued"}
    elif args.action == "activate":
        result = store.activate(
            args.candidate_id, expected_epoch=args.expected_epoch, now=datetime.now(UTC)
        )
    else:
        result = store.rollback(
            args.candidate_id,
            expected_epoch=args.expected_epoch,
            operator=args.operator,
            reason=args.reason,
        )
    print(json.dumps({k: v for k, v in result.items() if k not in {"PK", "SK"}}))


if __name__ == "__main__":
    main()
