"""Collision-safe source collection keys."""

from db.schema_v2 import digest


def collection_state_key(source: str, mode: str, scope: str = "") -> dict[str, str]:
    return {"PK": f"V2#COLLECT#{digest([source, mode, scope])}", "SK": "STATE"}


def collection_lease_key(source: str, mode: str, scope: str = "") -> dict[str, str]:
    return {**collection_state_key(source, mode, scope), "SK": "LEASE"}


def event_work_key(source: str, event_id: str, kind: str) -> dict[str, str]:
    return {"PK": f"V2#COLLECTION_WORK#{source}", "SK": f"{kind.upper()}#{digest(event_id)}"}
