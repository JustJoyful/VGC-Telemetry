from decimal import Decimal
from typing import Any

from boto3.dynamodb.conditions import Key

from db.client import get_dynamodb_table
from db.schema import regulation_pk, tournament_pk


def deserialize_dynamodb_item(item: Any) -> Any:
    """Recursively convert DynamoDB Decimal instances to standard Python int or float."""
    if isinstance(item, list):
        return [deserialize_dynamodb_item(v) for v in item]
    if isinstance(item, dict):
        return {k: deserialize_dynamodb_item(v) for k, v in item.items()}
    if isinstance(item, Decimal):
        return int(item) if item % 1 == 0 else float(item)
    return item


def query_teams_by_tournament(
    tid: str,
    limit: int = 20,
    exclusive_start_key: dict[str, Any] | None = None,
    table: Any | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Query team entries for a tournament with cursor pagination."""
    tbl = table or get_dynamodb_table()
    kwargs: dict[str, Any] = {
        "KeyConditionExpression": Key("PK").eq(tournament_pk(tid)) & Key("SK").begins_with("TEAM#"),
        "Limit": limit,
    }
    if exclusive_start_key:
        kwargs["ExclusiveStartKey"] = exclusive_start_key

    response = tbl.query(**kwargs)
    raw_items = response.get("Items", [])
    items = [deserialize_dynamodb_item(item) for item in raw_items]
    last_evaluated_key = response.get("LastEvaluatedKey")
    return items, last_evaluated_key


def query_player_history(
    player_id: str,
    limit: int = 50,
    table: Any | None = None,
) -> list[dict[str, Any]]:
    """Query GSI1 for a player's tournament history ordered by tournament date."""
    tbl = table or get_dynamodb_table()
    response = tbl.query(
        IndexName="GSI1",
        KeyConditionExpression=Key("GSI1PK").eq(f"PLAYER#{player_id}"),
        Limit=limit,
    )
    raw_items = response.get("Items", [])
    return [deserialize_dynamodb_item(item) for item in raw_items]


def query_meta_stat(
    regulation: str,
    sk_prefix: str,
    table: Any | None = None,
) -> list[dict[str, Any]]:
    """Query pre-aggregated metrics for a regulation partition."""
    tbl = table or get_dynamodb_table()
    key_cond = Key("PK").eq(regulation_pk(regulation)) & Key("SK").begins_with(sk_prefix)
    response = tbl.query(KeyConditionExpression=key_cond)
    raw_items = response.get("Items", [])
    return [deserialize_dynamodb_item(item) for item in raw_items]


def get_meta_item(
    regulation: str,
    sk: str,
    table: Any | None = None,
) -> dict[str, Any] | None:
    """Get a single pre-aggregated metric item."""
    tbl = table or get_dynamodb_table()
    response = tbl.get_item(Key={"PK": regulation_pk(regulation), "SK": sk})
    item = response.get("Item")
    return deserialize_dynamodb_item(item) if item else None
