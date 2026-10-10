"""Immutable S3 catalog evidence and conditional, audited DynamoDB activation."""

import hashlib
from datetime import UTC, datetime

from core.catalog_candidates import validate_candidate
from core.models.catalog import CatalogCandidate
from db.publication import get, transact
from db.schema_v2 import native
from ingestion.contributions import immutable_row
from ingestion.staging import _put_immutable, canonical_bytes

STATE_KEY = {"PK": "V2#CATALOG", "SK": "ACTIVE"}
PENDING_KEY = {"PK": "V2#CATALOG", "SK": "PENDING"}
READY_KEY = {"PK": "V2#CATALOG", "SK": "READY"}


class CatalogStore:
    def __init__(self, *, table, s3_client, bucket):
        self.table, self.s3, self.bucket = table, s3_client, bucket

    def state(self):
        return native(get(self.table, STATE_KEY) or {})

    def save(self, candidate):
        candidate = validate_candidate(candidate)
        body = canonical_bytes(candidate.model_dump(mode="json"))
        key = f"staging/v2/catalog/candidates/{candidate.candidate_id}.json"
        _put_immutable(key, body, s3_client=self.s3, bucket_name=self.bucket)
        snapshot = candidate.snapshot
        row = {
            "PK": "V2#CATALOG",
            "SK": "CANDIDATE#" + candidate.candidate_id,
            "candidate_id": candidate.candidate_id,
            "bucket": self.bucket,
            "key": key,
            "sha256": hashlib.sha256(body).hexdigest(),
            "status": candidate.status,
            "revision": snapshot.snapshot_id if snapshot else None,
            "historical_coverage": candidate.historical_coverage,
        }
        immutable_row(self.table, row)
        if snapshot:
            immutable_row(self.table, {**row, "SK": "REV#" + snapshot.snapshot_id})
        if candidate.status == "pending":
            # Each pending result is retained; the pointer is only a diagnostic hint.
            self.table.put_item(
                Item={
                    **PENDING_KEY,
                    "pending_revision": candidate.candidate_id,
                    "validation_errors": candidate.validation_errors,
                }
            )
        return row

    def load_candidate(self, candidate_id):
        row = get(self.table, {"PK": "V2#CATALOG", "SK": "CANDIDATE#" + candidate_id})
        if not row:
            raise ValueError("Catalog candidate not retained")
        body = self.s3.get_object(Bucket=row["bucket"], Key=row["key"])["Body"].read()
        if hashlib.sha256(body).hexdigest() != row["sha256"]:
            raise ValueError("Catalog evidence hash mismatch")
        candidate = validate_candidate(CatalogCandidate.model_validate_json(body))
        if candidate.candidate_id != candidate_id or candidate.status != row["status"]:
            raise ValueError("Catalog metadata differs from evidence")
        return candidate

    def load_revision(self, revision):
        row = get(self.table, {"PK": "V2#CATALOG", "SK": "REV#" + revision})
        if not row:
            raise ValueError("Catalog revision not retained")
        candidate = self.load_candidate(row["candidate_id"])
        if candidate.status != "verified" or candidate.snapshot.snapshot_id != revision:
            raise ValueError("Catalog revision is not verified")
        return candidate.snapshot

    def queue_activation(self, candidate_id, *, expected_epoch):
        candidate = self.load_candidate(candidate_id)
        if candidate.status != "verified":
            raise ValueError("Only verified candidates can be queued")
        if type(expected_epoch) is not int or expected_epoch < 0:
            raise ValueError("Expected catalog epoch must be nonnegative")
        guard = {"Key": STATE_KEY, "ConditionExpression": "attribute_not_exists(PK)"}
        if expected_epoch:
            guard.update(
                ConditionExpression="epoch = :expected",
                ExpressionAttributeValues={":expected": expected_epoch},
            )
        transact(
            self.table,
            [
                {"ConditionCheck": guard},
                {
                    "Put": {
                        "Item": {
                            **READY_KEY,
                            "candidate_id": candidate_id,
                            "expected_epoch": expected_epoch,
                        },
                        "ConditionExpression": "attribute_not_exists(PK) OR candidate_id = :id",
                        "ExpressionAttributeValues": {":id": candidate_id},
                    }
                },
            ],
        )

    def activate(self, candidate_id, *, expected_epoch, now):
        candidate = self.load_candidate(candidate_id)
        if candidate.status != "verified" or not candidate.snapshot.coverage_complete:
            raise ValueError("Only verified complete candidates can activate")
        snapshot = candidate.snapshot
        if now.tzinfo is None or not snapshot.effective_from or now < snapshot.effective_from:
            raise ValueError("Official effective time has not arrived")
        if snapshot.effective_until and now >= snapshot.effective_until:
            raise ValueError("Official effective interval has ended")
        current = self.state()
        previous = candidate.previous_snapshot
        if previous and current.get("active_revision") == previous.snapshot_id:
            if self.load_revision(previous.snapshot_id) != previous:
                raise ValueError("Candidate baseline differs from retained evidence")
        if current.get("active_revision") != (previous.snapshot_id if previous else None):
            if current.get("epoch", 0) == expected_epoch:
                raise ValueError("Candidate baseline differs from active catalog")
        return self._point(candidate, current, expected_epoch, action="activate", now=now)

    def rollback(self, candidate_id, *, expected_epoch, operator, reason, now=None):
        if not operator.strip() or not reason.strip():
            raise ValueError("Rollback requires operator and reason")
        candidate = self.load_candidate(candidate_id)
        if candidate.status != "verified" or not candidate.snapshot.coverage_complete:
            raise ValueError("Rollback target must be retained and verified")
        return self._point(
            candidate,
            self.state(),
            expected_epoch,
            action="rollback",
            now=now or datetime.now(UTC),
            operator=operator,
            reason=reason,
        )

    def _point(self, candidate, current, expected_epoch, *, action, now, **audit):
        if type(expected_epoch) is not int or expected_epoch < 0:
            raise ValueError("Expected catalog epoch must be nonnegative")
        snapshot = candidate.snapshot
        state = {
            **STATE_KEY,
            "active_revision": snapshot.snapshot_id,
            "candidate_id": candidate.candidate_id,
            "epoch": expected_epoch + 1,
            "verification_status": "verified",
            "activated_at": now.isoformat(),
            "effective_from": snapshot.effective_from.isoformat(),
            "historical_coverage": candidate.historical_coverage,
        }
        put = {"Item": state, "ConditionExpression": "attribute_not_exists(PK)"}
        if expected_epoch:
            put.update(
                ConditionExpression="epoch = :expected",
                ExpressionAttributeValues={":expected": expected_epoch},
            )
        record = {
            "PK": "V2#CATALOG",
            "SK": f"AUDIT#{expected_epoch + 1}",
            "action": action,
            "from_revision": current.get("active_revision"),
            "to_revision": snapshot.snapshot_id,
            "at": now.isoformat(),
            **audit,
        }
        transact(
            self.table,
            [
                {"Put": put},
                {"Put": {"Item": record, "ConditionExpression": "attribute_not_exists(PK)"}},
            ],
        )
        return state
