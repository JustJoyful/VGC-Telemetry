"""Expiring owner-bound leases; tokens fence reacquisitions by the same owner."""

import time
from collections.abc import Callable
from uuid import uuid4

from core.models.publication import Lease


def clock_now() -> int:
    return int(time.time())


def elapsed_clock(now: int) -> Callable[[], int]:
    """Advance a supplied epoch with monotonic elapsed time, including in offline tests."""
    started = time.monotonic()
    return lambda: now + int(time.monotonic() - started)


def acquire_lease(*, table, key: dict, owner: str, now: int, ttl: int) -> Lease:
    if not owner or ttl <= 0 or not key["PK"].startswith("V2#") or key["SK"] != "LEASE":
        raise ValueError("Expected schema-2 lease key, owner and positive TTL")
    result = Lease(key=key, owner=owner, token=uuid4().hex, expires_at=now + ttl, ttl=ttl)
    table.put_item(
        Item={**key, **result.model_dump(exclude={"key"})},
        ConditionExpression="attribute_not_exists(PK) OR expires_at <= :now",
        ExpressionAttributeValues={":now": now},
    )
    return result


def lease_guard(lease: Lease, now: int) -> dict:
    return {
        "ConditionCheck": {
            "Key": lease.key,
            "ConditionExpression": "#owner = :owner AND #token = :token AND expires_at > :now",
            "ExpressionAttributeNames": {"#owner": "owner", "#token": "token"},
            "ExpressionAttributeValues": {
                ":owner": lease.owner,
                ":token": lease.token,
                ":now": now,
            },
        }
    }


def renew_lease(lease: Lease, *, table, now: int) -> Lease:
    args = lease_guard(lease, now)["ConditionCheck"]
    values = args.pop("ExpressionAttributeValues")
    table.update_item(
        **args,
        UpdateExpression="SET expires_at = :expiry",
        ExpressionAttributeValues={**values, ":expiry": now + lease.ttl},
    )
    return lease.model_copy(update={"expires_at": now + lease.ttl})


def release_lease(lease: Lease, *, table) -> None:
    table.delete_item(
        Key=lease.key,
        ConditionExpression="#owner = :owner AND #token = :token",
        ExpressionAttributeNames={"#owner": "owner", "#token": "token"},
        ExpressionAttributeValues={":owner": lease.owner, ":token": lease.token},
    )
