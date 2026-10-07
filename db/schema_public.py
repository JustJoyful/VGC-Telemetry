"""Central public namespace keys; sections are hashed, public ordinals stay opaque."""

from db.schema_v2 import digest


def public_state_key() -> dict[str, str]:
    return {"PK": "V2#PUBLIC", "SK": "STATE"}


def public_capture_key() -> dict[str, str]:
    return {"PK": "V2#PUBLIC", "SK": "CAPTURE"}


def public_member_key(event_id: str) -> dict[str, str]:
    return {"PK": "V2#PUBLIC#INPUTS", "SK": digest(event_id)}


def public_lease_key() -> dict[str, str]:
    return {"PK": "V2#PUBLIC", "SK": "LEASE"}


def snapshot_pk(snapshot_id: str, section: str) -> str:
    return f"V2#PUBLIC#SNAP#{digest(snapshot_id)}#{digest(section)}"


def snapshot_row_key(snapshot_id: str, section: str, ordinal: str) -> dict[str, str]:
    return {"PK": snapshot_pk(snapshot_id, section), "SK": ordinal}
