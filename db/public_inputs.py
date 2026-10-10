"""Atomic public directory actions; exact refs selected under event promotion CAS."""

from datetime import UTC, datetime

from botocore.exceptions import ClientError

from core.models.public_read import PublicInput
from core.models.publication import RevisionInput
from db.schema_public import public_capture_key, public_member_key, public_state_key
from db.schema_v2 import native
from ingestion.contributions import check_size
from ingestion.revisions import revision_id


class PendingPublicOrder(ValueError):
    """Equal-order observations conflict; require explicit later evidence."""


def note_public_capture(order: int, *, table) -> None:
    """Monotonic observed valid native capture time, not publication or source check."""
    if type(order) is not int or order < 0:
        raise ValueError("Capture order must be a nonnegative native integer")
    try:
        table.update_item(
            Key=public_capture_key(),
            UpdateExpression="SET capture_order = :order, captured_at = :at",
            ConditionExpression="attribute_not_exists(capture_order) OR capture_order < :order",
            ExpressionAttributeValues={
                ":order": order,
                ":at": datetime.fromtimestamp(order / 1_000_000, UTC).isoformat(),
            },
        )
    except ClientError as error:
        if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise


def public_input(observation: RevisionInput) -> PublicInput:
    return PublicInput(
        **observation.model_dump(include=set(PublicInput.model_fields) - {"revision_id"}),
        revision_id=revision_id(observation),
    )


def public_registration_actions(
    input: PublicInput,
    *,
    current_pointer: dict | None,
    current_member: dict | None,
    validated_at: int | None = None,
) -> list[dict]:
    if current_pointer and input.order < current_pointer["order"]:
        return []
    row = {**public_member_key(input.event_id), **input.model_dump(mode="json")}
    if current_member:
        if input.order < current_member["order"]:
            return []
        if input.order == current_member["order"]:
            if row == current_member:
                return []
            raise PendingPublicOrder("Equal-order public capture conflict")
    check_size(row)
    put = {"Item": row, "ConditionExpression": "attribute_not_exists(PK)"}
    if current_member:
        put.update(
            ConditionExpression="#order = :order AND revision_id = :rid AND capture_hash = :hash",
            ExpressionAttributeNames={"#order": "order"},
            ExpressionAttributeValues={
                ":order": current_member["order"],
                ":rid": current_member["revision_id"],
                ":hash": current_member["capture_hash"],
            },
        )
    return [
        {"Put": put},
        {
            "Update": {
                "Key": public_state_key(),
                "UpdateExpression": (
                    "SET dirty = :true, "
                    "first_validated_at = if_not_exists(first_validated_at, :at) ADD epoch :one"
                ),
                "ExpressionAttributeValues": {
                    ":true": True,
                    ":one": 1,
                    ":at": validated_at
                    if validated_at is not None
                    else int(datetime.now(UTC).timestamp()),
                },
            }
        },
    ]


def observation_from_metadata(
    metadata: dict, *, event_id: str, rid: str, order: int
) -> PublicInput:
    """Validate historical OBS metadata; never select the latest mutable OBS row."""
    fields = set(PublicInput.model_fields) - {"revision_id", "derivation_version"}
    value = PublicInput(**native({k: metadata[k] for k in fields}), revision_id=rid)
    from db.schema_v2 import digest

    calculated = digest(
        [
            metadata["source"],
            value.event_id,
            value.semantic_hash,
            metadata["adapter_version"],
            metadata["schema_version"],
            value.catalog_revision,
        ]
    )
    if calculated != rid or value.event_id != event_id or value.order != order:
        raise ValueError("Observation metadata differs from accepted pointer identity/order")
    return value
