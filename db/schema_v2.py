"""Separate bounded schema-2 key namespaces. Hash components avoid delimiter collisions."""

import hashlib
from decimal import Decimal

from ingestion.staging import canonical_bytes


def native(v):
    if isinstance(v, Decimal):
        return int(v) if v == int(v) else str(v)
    if isinstance(v, dict):
        return {k: native(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [native(x) for x in v]
    return v


def digest(value) -> str:
    return hashlib.sha256(canonical_bytes(native(value))).hexdigest()


def event_key(event_id: str) -> dict[str, str]:
    return {"PK": f"V2#EVENT#{digest(event_id)}", "SK": "STATE"}


def revision_pk(event_id: str, revision_id: str) -> str:
    return f"V2#REV#{digest(event_id)}#{revision_id}"


def population_key(population_id: str) -> dict[str, str]:
    return {"PK": f"V2#POP#{population_id}", "SK": "STATE"}


def member_key(population_id: str, event_id: str) -> dict[str, str]:
    return {"PK": population_key(population_id)["PK"], "SK": f"MEMBER#{digest(event_id)}"}


def lease_key(key: dict[str, str]) -> dict[str, str]:
    return {"PK": key["PK"], "SK": "LEASE"}


def generation_pk(population_id: str, generation_id: str) -> str:
    return f"V2#GEN#{population_id}#{generation_id}"


def intent_key(
    event_id: str, revision_id: str, population_id: str, order: int | None = None
) -> dict[str, str]:
    suffix = f"#{order}" if order is not None else ""
    return {
        "PK": "V2#WORK",
        "SK": f"INTENT#{digest(event_id)}#{revision_id}#{population_id}{suffix}",
    }


def dirty_key(population_id: str) -> dict[str, str]:
    return {"PK": "V2#DIRTY", "SK": population_id}
