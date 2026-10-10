"""Bounded supplementary links, immutable revisions, and serialized exact-team updates."""

import hashlib

from core.models.builds import BuildLink, BuildRecord
from db.publication import get, transact
from db.schema_v2 import digest, native
from ingestion.build_links import resolve_build
from ingestion.contributions import immutable_row, query_rows
from ingestion.staging import _put_immutable, canonical_bytes


def team_pk(source, event_id, entry_id):
    return "V2#BUILD#" + digest([source, event_id, entry_id])


class BuildStore:
    def __init__(self, *, table, s3_client, bucket):
        self.table, self.s3, self.bucket = table, s3_client, bucket

    def save(self, record):
        from core.build_evidence import normalize_public_build

        checked = normalize_public_build(
            record.document, source=record.admission, captured_at=record.evidence.captured_at
        )
        if checked != record:
            raise ValueError("Build differs from normalized public evidence")
        body = canonical_bytes(record.model_dump(mode="json"))
        key = f"staging/v2/builds/{record.revision_id}.json"
        _put_immutable(key, body, s3_client=self.s3, bucket_name=self.bucket)
        immutable_row(
            self.table,
            {
                "PK": "V2#BUILD",
                "SK": "REV#" + record.revision_id,
                "bucket": self.bucket,
                "key": key,
                "sha256": hashlib.sha256(body).hexdigest(),
            },
        )

    def load(self, revision_id):
        row = get(self.table, {"PK": "V2#BUILD", "SK": "REV#" + revision_id})
        if not row:
            raise ValueError("Build revision not retained")
        body = self.s3.get_object(Bucket=row["bucket"], Key=row["key"])["Body"].read()
        if hashlib.sha256(body).hexdigest() != row["sha256"]:
            raise ValueError("Build revision hash mismatch")
        result = BuildRecord.model_validate_json(body)
        if result.revision_id != revision_id:
            raise ValueError("Build revision identity mismatch")
        return result

    def links(self, source, event_id, entry_id, *, limit=100, cursor=None):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Link page must be 1–100")
        pk = team_pk(source, event_id, entry_id)
        if cursor and (cursor.get("PK") != pk or not cursor.get("SK", "").startswith("REV#")):
            raise ValueError("Link cursor belongs to another team")
        return query_rows(self.table, pk, "REV#", limit=limit, cursor=cursor)

    def ingest(self, record, tournaments, *, cross_reference=None, event_revisions=None):
        if len(tournaments) > 100:
            raise ValueError("Build matching requires bounded explicit tournaments")
        self.save(record)
        result = resolve_build(record, tournaments, cross_reference=cross_reference)
        pk = (
            team_pk(result.event_source, result.event_id, result.entry_id)
            if result.entry_id
            else "V2#BUILD"
        )
        result_key = {
            "PK": pk,
            "SK": "REV#" + record.revision_id
            if result.entry_id
            else "RESULT#" + record.revision_id,
        }
        if result.entry_id and event_revisions:
            result_key["SK"] += "#" + digest(event_revisions[result.event_id])
        existing = get(self.table, result_key)
        if existing:
            return BuildLink.model_validate(native(existing["result"]))
        state_key = {"PK": pk, "SK": "STATE"}
        state = native(get(self.table, state_key) or {}) if result.entry_id else {}
        epoch = state.get("epoch", 0)
        if result.status == "linked" or result.diagnostics == ["correction_target_missing"]:
            active = query_rows(self.table, pk, "ACTIVE#", limit=100)
            if active.get("LastEvaluatedKey"):
                result = result.model_copy(
                    update={"status": "pending_conflict", "diagnostics": ["link_capacity_exceeded"]}
                )
            else:
                previous_ids = {row["revision_id"] for row in active["Items"]} | set(
                    state.get("conflict_ids", [])
                )
                previous = [self.load(rid) for rid in sorted(previous_ids)]
                result = resolve_build(
                    record, tournaments, previous_builds=previous, cross_reference=cross_reference
                )
                for row in active["Items"]:
                    old = next(p for p in previous if p.revision_id == row["revision_id"])
                    if (old.source, old.build_id) == (record.source, record.build_id) and (
                        old.document.content_hash != record.document.content_hash
                        and record.supersedes != old.revision_id
                    ):
                        result = result.model_copy(
                            update={
                                "status": "pending_conflict",
                                "diagnostics": sorted(
                                    set(result.diagnostics + ["correction_evidence_required"])
                                ),
                            }
                        )
        row = {**result_key, "result": result.model_dump(mode="json")}
        if result.entry_id and event_revisions:
            result = result.model_copy(update={"event_revision": event_revisions[result.event_id]})
            row["result"] = result.model_dump(mode="json")
        actions = [{"Put": {"Item": row, "ConditionExpression": "attribute_not_exists(PK)"}}]
        if result.entry_id:
            conflict_ids = set(state.get("conflict_ids", []))
            if result.status == "linked" and record.supersedes:
                # An explicit correction resolves compatible pending evidence from this publication.
                for old in previous:
                    if (old.source, old.build_id, old.author) == (
                        record.source,
                        record.build_id,
                        record.author,
                    ):
                        conflict_ids.discard(old.revision_id)
            if result.status == "pending_conflict":
                conflict_ids.add(record.revision_id)
            if len(conflict_ids) > 100:
                raise ValueError(
                    "Build conflict capacity exceeded; explicit reconciliation required"
                )
            guard = {
                "Item": {**state_key, "epoch": epoch + 1, "conflict_ids": sorted(conflict_ids)},
                "ConditionExpression": "attribute_not_exists(PK)",
            }
            if state:
                guard.update(
                    ConditionExpression="epoch = :epoch",
                    ExpressionAttributeValues={":epoch": epoch},
                )
            actions.append({"Put": guard})
        if result.status == "linked":
            actions.append(
                {
                    "Put": {
                        "Item": {
                            "PK": pk,
                            "SK": "ACTIVE#" + digest([record.source, record.build_id]),
                            "revision_id": record.revision_id,
                        }
                    }
                }
            )
            if event_revisions:
                from db.schema_v2 import event_key

                actions.append(
                    {
                        "ConditionCheck": {
                            "Key": event_key(result.event_id),
                            "ConditionExpression": "revision_id = :revision",
                            "ExpressionAttributeValues": {":revision": result.event_revision},
                        }
                    }
                )
        transact(self.table, actions)
        return result
