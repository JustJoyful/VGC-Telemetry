"""Immutable Phase 0 objects, deliberately outside the legacy raw/ notification prefix."""

import hashlib
import json
from typing import Any
from urllib.parse import quote

from botocore.exceptions import ClientError

from core.models.champions import NormalizationResult, SourceCapture
from ingestion.normalize import normalized_content_hash


def canonical_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _put_immutable(key, body, *, s3_client, bucket_name):
    try:
        s3_client.put_object(
            Bucket=bucket_name, Key=key, Body=body, ContentType="application/json", IfNoneMatch="*"
        )
    except ClientError as error:
        if error.response["Error"]["Code"] not in ("PreconditionFailed", "412"):
            raise
        existing = s3_client.get_object(Bucket=bucket_name, Key=key)["Body"].read()
        if existing != body:
            raise ValueError("Immutable staging object differs from expected content") from error
    return key


def stage_capture(capture: SourceCapture, *, s3_client: Any, bucket_name: str) -> str:
    # Revalidate copies supplied by callers before deriving immutable content identity.
    capture = SourceCapture.model_validate(capture.model_dump(mode="json"))
    body = canonical_bytes(capture.model_dump(mode="json"))
    digest = hashlib.sha256(body).hexdigest()
    event_id = quote(capture.source_event_id, safe="")
    key = f"staging/v2/captures/{capture.source}/{event_id}/{digest}.json"
    return _put_immutable(key, body, s3_client=s3_client, bucket_name=bucket_name)


def stage_normalized(
    result: NormalizationResult,
    *,
    capture_key: str,
    s3_client: Any,
    bucket_name: str,
    capture_version_id: str | None = None,
) -> str:
    if not capture_key.startswith("staging/v2/captures/"):
        raise ValueError("Normalized staging requires an isolated capture reference")
    result = NormalizationResult.model_validate(result.model_dump(mode="json"))
    payload = {
        "schema_version": 2,
        "capture_key": capture_key,
        "result": result.model_dump(mode="json"),
        "semantic_hash": normalized_content_hash(result.tournament) if result.tournament else None,
    }
    if capture_version_id:
        payload["capture_version_id"] = capture_version_id
    body = canonical_bytes(payload)
    digest = hashlib.sha256(body).hexdigest()
    return _put_immutable(
        f"staging/v2/normalized/{digest}.json", body, s3_client=s3_client, bucket_name=bucket_name
    )
