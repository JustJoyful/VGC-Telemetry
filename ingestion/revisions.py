"""Exact immutable input reads and timestamp-independent semantic identities."""

import hashlib
import json
import re
from pathlib import Path

from pydantic import ValidationError

from core.catalog import load_catalog
from core.models.champions import SourceCapture
from core.models.publication import RevisionInput, StagedEnvelope
from core.source_admission import load_admissions
from db.schema_v2 import digest
from ingestion.normalize import normalized_content_hash


class InvalidRevisionInput(ValueError):
    """Invalid staged data, distinct from a programming or configuration failure."""


def validated(model, value):
    try:
        return model.model_validate(value)
    except ValidationError as error:
        raise InvalidRevisionInput(str(error)) from error


def _read(*, s3_client, bucket, key, version_id, prefix):
    if not key.startswith(prefix):
        raise InvalidRevisionInput("Input is outside the isolated staging namespace")
    match = re.search(r"/([a-f0-9]{64})\.json$", key)
    if not version_id and not match:
        raise InvalidRevisionInput("Unversioned inputs require a hash-addressed immutable key")
    args = {"Bucket": bucket, "Key": key}
    if version_id:
        args["VersionId"] = version_id
    body = s3_client.get_object(**args)["Body"].read()
    body_hash = hashlib.sha256(body).hexdigest()
    if match and match[1] != body_hash:
        raise InvalidRevisionInput("Stored object content does not match its immutable address")
    try:
        return json.loads(body), body_hash
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise InvalidRevisionInput(str(error)) from error


def read_staged_result(
    *, s3_client, bucket: str, key: str, version_id: str | None, catalog_store=None
) -> RevisionInput:
    payload, _ = _read(
        s3_client=s3_client,
        bucket=bucket,
        key=key,
        version_id=version_id,
        prefix="staging/v2/normalized/",
    )
    envelope = validated(StagedEnvelope, payload)
    result = envelope.result
    if result.admission_status != "verified":
        raise InvalidRevisionInput("Only verified results qualify for publication")
    capture_key = envelope.capture_key
    cv = envelope.capture_version_id
    raw, capture_hash = _read(
        s3_client=s3_client,
        bucket=bucket,
        key=capture_key,
        version_id=cv,
        prefix="staging/v2/captures/",
    )
    capture = validated(SourceCapture, raw)
    tournament = result.tournament
    event_id = f"{capture.source}:{capture.source_event_id}"
    if tournament.metadata.event_id != event_id:
        raise InvalidRevisionInput("Capture and normalized event identities differ")
    semantic_hash = normalized_content_hash(tournament)
    if semantic_hash != envelope.semantic_hash:
        raise InvalidRevisionInput("Normalized semantic hash mismatch")
    catalog = load_catalog(
        tournament.metadata.catalog_revision,
        index_path=Path("core/regulations/v2/index.json"),
        store=catalog_store,
    )
    trusted = [ref.model_dump(mode="json") for ref in catalog.evidence]
    for admission in load_admissions(Path("core/sources/admission.json")):
        if admission.source == capture.source:
            trusted.extend(ref.model_dump(mode="json") for ref in admission.evidence)
            for mapping in admission.format_mappings.values():
                trusted.extend(ref.model_dump(mode="json") for ref in mapping.evidence)

    def evidence(value):
        if isinstance(value, dict):
            if {"document_key", "content_hash", "url", "captured_at"} <= value.keys():
                doc = capture.documents.get(value["document_key"])
                if value in trusted:
                    return
                if not doc or doc.content_hash != value["content_hash"] or doc.url != value["url"]:
                    raise InvalidRevisionInput(
                        "Evidence does not resolve to the referenced capture"
                    )
            for nested in value.values():
                evidence(nested)
        elif isinstance(value, list):
            for nested in value:
                evidence(nested)

    evidence(result.model_dump(mode="json"))
    return RevisionInput(
        result=result,
        bucket=bucket,
        normalized_key=key,
        normalized_version_id=version_id,
        capture_key=capture_key,
        capture_version_id=cv,
        capture_hash=capture_hash,
        semantic_hash=semantic_hash,
        source=capture.source,
        event_id=event_id,
        adapter_version=capture.adapter_version,
        catalog_revision=tournament.metadata.catalog_revision,
        captured_at=capture.fetched_at,
        order=int(capture.fetched_at.timestamp() * 1_000_000),
    )


def revision_id(input: RevisionInput) -> str:
    return digest(
        [
            input.source,
            input.event_id,
            input.semantic_hash,
            input.adapter_version,
            input.schema_version,
            input.catalog_revision,
        ]
    )
