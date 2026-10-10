"""Bounded 24-hour public source check; changed sources stay pending resolution."""

from datetime import UTC, datetime
from pathlib import Path

from core.catalog import load_catalog
from core.catalog_candidates import build_candidate
from db.catalog import READY_KEY, CatalogStore
from db.publication import get
from db.schema_v2 import digest, native
from ingestion.staging import _put_immutable, canonical_bytes
from scraper.http import captured_document, request_public


def check_references(*, table, s3_client, bucket, client, now=None, telemetry=None):
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("Reference check requires aware time")
    key = {"PK": "V2#CATALOG", "SK": "CHECK"}
    previous = native(table.get_item(Key=key, ConsistentRead=True).get("Item", {}))
    store = CatalogStore(table=table, s3_client=s3_client, bucket=bucket)
    ready = get(table, READY_KEY)
    current = store.state().get("active_revision")
    active_catalog = store.load_revision(current) if current else None
    urls = {
        "https://champions.pokemon.com/en-us/news/",
        "https://api.github.com/repos/smogon/pokemon-showdown/commits/master",
    }
    for revision in ("champions-mb-3661ce4", "champions-mc-3661ce4"):
        catalog = load_catalog(revision, index_path=Path("core/regulations/v2/index.json"))
        urls.update(ref.url for ref in catalog.evidence)
    if ready:
        urls.update(
            ref.url for ref in store.load_candidate(ready["candidate_id"]).snapshot.evidence
        )
    if active_catalog:
        urls.update(ref.url for ref in active_catalog.evidence)
    if len(urls) > 40:
        raise ValueError("Reference source set exceeds bounded check")
    hashes, changed, errors, documents = {}, [], [], {}
    for url in sorted(urls):
        try:
            document = captured_document(
                request_public(client, url, on_retry=telemetry.retry if telemetry else None)
            )
            documents[url] = document
            body = canonical_bytes(
                {"captured_at": now.isoformat(), "document": document.model_dump(mode="json")}
            )
            _put_immutable(
                f"staging/v2/catalog/checks/{document.content_hash}/{digest(body.decode())}.json",
                body,
                s3_client=s3_client,
                bucket_name=bucket,
            )
            if document.status != 200:
                errors.append("http_failure")
                if telemetry:
                    telemetry.failure(RuntimeError("HTTP refusal"), result="pending")
                continue
            hashes[url] = document.content_hash
            if previous.get("hashes", {}).get(url) != document.content_hash:
                changed.append(url)
        except Exception as error:
            errors.append(type(error).__name__)
            if telemetry:
                telemetry.failure(error, result="pending")
    # The check pointer is CAS protected; a stale invocation cannot erase newer failures.
    values = {
        ":hashes": hashes,
        ":at": now.isoformat(),
        ":errors": sorted(set(errors)),
        ":pending": bool(changed or errors or previous.get("pending", False)),
    }
    condition = "attribute_not_exists(checked_at)"
    if previous:
        condition = "checked_at = :previous"
        values[":previous"] = previous["checked_at"]
        if datetime.fromisoformat(previous["checked_at"]) > now:
            raise ValueError("Stale reference check")
    expression = "SET hashes = :hashes, checked_at = :at, errors = :errors, pending = :pending"
    if not errors:
        expression += ", last_successful_check = :at"
    table.update_item(
        Key=key,
        UpdateExpression=expression,
        ConditionExpression=condition,
        ExpressionAttributeValues=values,
    )
    # Reuse the bundled resolved export; any changed supporting evidence fails closed.
    baseline = active_catalog or load_catalog(
        "champions-mc-3661ce4", index_path=Path("core/regulations/v2/index.json")
    )
    candidate = build_candidate(
        baseline.model_dump(mode="json"),
        previous=active_catalog,
        documents={
            ref.url: documents[ref.url] for ref in baseline.evidence if ref.url in documents
        },
    )
    store.save(candidate)
    if telemetry:
        telemetry.emit("CatalogCandidates")
    activation = activate_ready(store, documents=documents, now=now)
    unresolved = candidate.status != "verified" or activation.startswith("pending_")
    if unresolved and not values[":pending"]:
        table.update_item(
            Key=key,
            UpdateExpression="SET pending = :pending",
            ConditionExpression="checked_at = :at",
            ExpressionAttributeValues={":pending": True, ":at": now.isoformat()},
        )
        values[":pending"] = True
    if telemetry:
        if unresolved:
            telemetry.emit("CatalogPending")
            telemetry.failure(ValueError("Unresolved catalog"), result="pending")
        if activation == "activated":
            telemetry.emit("CatalogActivations")
        if not errors:
            previous_success = previous.get("last_successful_check")
            telemetry.emit(
                "ReferenceCheckAge",
                max(0, int((now - datetime.fromisoformat(previous_success)).total_seconds()))
                if previous_success
                else 0,
            )
    return {
        "status": "pending" if values[":pending"] else "success",
        "changed": changed,
        "errors": sorted(set(errors)),
        "build_acquisition": "disabled",
        "candidate_id": candidate.candidate_id,
        "candidate_status": candidate.status,
        "activation": activation,
    }


def activate_ready(store, *, documents, now):
    ready = native(get(store.table, READY_KEY) or {})
    if not ready:
        return "none"
    candidate = store.load_candidate(ready["candidate_id"])
    if candidate.status != "verified":
        raise ValueError("Queued catalog is not verified")
    if any(
        ref.url not in documents
        or documents[ref.url].status != 200
        or documents[ref.url].content_hash != ref.content_hash
        for ref in candidate.snapshot.evidence
    ):
        return "pending_evidence"
    current = store.state()
    duplicate = (
        current.get("candidate_id") == candidate.candidate_id
        and current.get("epoch") == ready["expected_epoch"] + 1
    )
    if not duplicate:
        if now < candidate.snapshot.effective_from:
            return "waiting_effective"
        if candidate.snapshot.effective_until and now >= candidate.snapshot.effective_until:
            return "pending_effective_interval"
        if current.get("epoch", 0) != ready["expected_epoch"]:
            return "pending_stale_pointer"
        store.activate(candidate.candidate_id, expected_epoch=ready["expected_epoch"], now=now)
    store.table.delete_item(
        Key=READY_KEY,
        ConditionExpression="candidate_id = :id AND expected_epoch = :epoch",
        ExpressionAttributeValues={
            ":id": candidate.candidate_id,
            ":epoch": ready["expected_epoch"],
        },
    )
    return "activated" if not duplicate else "already_active"
