"""Explicit public build evidence against exact, currently pinned native event inputs."""

import os

import boto3
from pydantic import ValidationError

from core.build_evidence import normalize_public_build
from core.models.builds import BuildCrossReference, BuildSource
from core.models.champions import CapturedDocument
from core.telemetry import RunTelemetry
from db.builds import BuildStore
from db.catalog import CatalogStore
from db.client import get_dynamodb_table
from db.publication import get
from db.schema_v2 import event_key
from ingestion.revisions import read_staged_result, revision_id


def handle_build_event(event, context=None, *, table=None, s3_client=None, bucket=None):
    telemetry = RunTelemetry("build", context=context)
    try:
        result = _handle_build_event(event, table=table, s3_client=s3_client, bucket=bucket)
    except Exception as error:
        telemetry.failure(error)
        telemetry.finish("error")
        raise
    if result["status"] == "linked":
        telemetry.emit("BuildLinks")
        telemetry.emit("AcceptedWork")
    elif result["status"] == "pending_conflict":
        telemetry.emit("BuildConflicts")
        telemetry.emit("PendingWork")
        telemetry.failure(
            ValueError("Build conflict"),
            event_ref=result.get("event_id"),
            result="pending_conflict",
        )
    else:
        telemetry.emit("PendingWork")
    telemetry.finish(result["status"])
    return result


def _handle_build_event(event, *, table, s3_client, bucket):
    refs = event.get("normalized", [])
    if not isinstance(refs, list) or not 1 <= len(refs) <= 10:
        raise ValueError("Build linking requires 1–10 exact native input references")
    table = table if table is not None else get_dynamodb_table()
    s3_client = s3_client if s3_client is not None else boto3.client("s3")
    bucket = bucket or os.environ["S3_RAW_BUCKET_NAME"]
    supplied = event["build"]
    try:
        record = normalize_public_build(
            CapturedDocument.model_validate(supplied["document"]),
            source=BuildSource.model_validate(supplied["source"]),
            captured_at=supplied["captured_at"],
        )
        cross = (
            BuildCrossReference.model_validate(event["cross_reference"])
            if event.get("cross_reference")
            else None
        )
    except (ValidationError, ValueError):
        raise ValueError("Invalid public build evidence") from None
    tournaments, revisions = [], {}
    for ref in refs:
        value = read_staged_result(
            s3_client=s3_client,
            bucket=ref["bucket"],
            key=ref["key"],
            version_id=ref.get("version_id"),
            catalog_store=CatalogStore(table=table, s3_client=s3_client, bucket=ref["bucket"]),
        )
        rid = revision_id(value)
        pointer = get(table, event_key(value.event_id)) or {}
        if pointer.get("revision_id") != rid:
            raise ValueError("Build target differs from current pinned native event revision")
        tournaments.append(value.result.tournament)
        revisions[value.event_id] = rid
    return (
        BuildStore(table=table, s3_client=s3_client, bucket=bucket)
        .ingest(
            record,
            tournaments,
            cross_reference=cross,
            event_revisions=revisions,
        )
        .model_dump(mode="json")
    )
