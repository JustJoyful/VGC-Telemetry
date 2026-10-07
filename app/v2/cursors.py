"""HMAC-authenticated public tokens, with strict bounded decoding."""

import base64
import binascii
import hashlib
import hmac
import json
import re

from app.models.v2 import SnapshotInfo
from app.v2.errors import V2Error, unavailable
from ingestion.staging import canonical_bytes


def _secret(secret):
    if not isinstance(secret, bytes) or len(secret) < 32:
        raise unavailable("Cursor configuration is unavailable")


def _b64(value):
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _encode(payload, secret):
    _secret(secret)
    body = canonical_bytes(payload)
    token = _b64(body) + "." + _b64(hmac.new(secret, body, hashlib.sha256).digest())
    if len(token) > 4096:
        raise V2Error(400, "invalid_cursor", "Token exceeds supported size")
    return token


def _invalid():
    return V2Error(400, "invalid_cursor", "Invalid or mismatched cursor")


def _decode(token, *, now, secret, kind):
    _secret(secret)
    if (
        not isinstance(token, str)
        or len(token.encode("utf-8")) > 4096
        or not re.fullmatch(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", token)
    ):
        raise _invalid()
    try:
        encoded, signed = token.split(".")
        body = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
        signature = base64.b64decode(
            signed + "=" * (-len(signed) % 4), altchars=b"-_", validate=True
        )
        if not hmac.compare_digest(signature, hmac.new(secret, body, hashlib.sha256).digest()):
            raise _invalid()
        # Reject noncanonical base64 spellings and duplicate JSON keys.
        if _b64(body) != encoded or _b64(signature) != signed:
            raise _invalid()

        def unique(pairs):
            data = {}
            for key, value in pairs:
                if key in data:
                    raise ValueError("Duplicate key")
                data[key] = value
            return data

        value = json.loads(body, object_pairs_hook=unique)
        keys = {"v", "kind", "snapshot_id", "exp"}
        if kind == "cursor":
            keys |= {"endpoint", "query_hash", "limit", "position"}
            if isinstance(value, dict) and "catalog_revision" in value:
                keys.add("catalog_revision")
                if not isinstance(value["catalog_revision"], str) or not value["catalog_revision"]:
                    raise _invalid()
        if (
            not isinstance(value, dict)
            or set(value) != keys
            or type(value["v"]) is not int
            or value["v"] != 1
            or value["kind"] != kind
            or not isinstance(value["snapshot_id"], str)
            or not value["snapshot_id"]
            or type(value["exp"]) is not int
        ):
            raise _invalid()
    except (ValueError, UnicodeDecodeError, binascii.Error) as error:
        raise _invalid() from error
    if value["exp"] <= now:
        raise V2Error(410, "cursor_expired", "Cursor has expired")
    return value


def encode_snapshot(snapshot_id: str, *, expires_at: int, secret: bytes) -> str:
    return _encode(
        {"v": 1, "kind": "snapshot", "snapshot_id": snapshot_id, "exp": expires_at}, secret
    )


def decode_snapshot(token: str, *, now: int, secret: bytes) -> SnapshotInfo:
    value = _decode(token, now=now, secret=secret, kind="snapshot")
    return SnapshotInfo(snapshot_id=value["snapshot_id"], expires_at=value["exp"])


def encode_cursor(
    *,
    snapshot: SnapshotInfo,
    endpoint: str,
    query: dict,
    limit: int,
    position: str,
    secret: bytes,
    catalog_revision: str | None = None,
) -> str:
    if not isinstance(position, str):
        raise _invalid()
    payload = {
        "v": 1,
        "kind": "cursor",
        "snapshot_id": snapshot.snapshot_id,
        "exp": snapshot.expires_at,
        "endpoint": endpoint,
        "query_hash": hashlib.sha256(canonical_bytes(query)).hexdigest(),
        "limit": limit,
        "position": position,
    }
    if catalog_revision is not None:
        payload["catalog_revision"] = catalog_revision
    return _encode(payload, secret)


def decode_cursor(
    token: str, *, endpoint: str, query: dict, limit: int, now: int, secret: bytes
) -> dict:
    value = _decode(token, now=now, secret=secret, kind="cursor")
    if (
        value["endpoint"] != endpoint
        or value["query_hash"] != hashlib.sha256(canonical_bytes(query)).hexdigest()
        or type(value["limit"]) is not int
        or value["limit"] != limit
        or not isinstance(value["position"], str)
    ):
        raise _invalid()
    return value
