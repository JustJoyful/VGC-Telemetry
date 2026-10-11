import os
import random as randomness
import time
from typing import Any

from botocore.config import Config

from core.aws import DEMO_NAME, aws_resource, local_mode, validate_local
from ingestion.retry import WriteExhausted, retry


def get_dynamodb_resource() -> Any:
    """Return configured boto3 DynamoDB resource."""
    region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    config = Config(
        retries={"max_attempts": 5, "mode": "standard"},
    )
    return aws_resource("dynamodb", region_name=region, config=config)


def get_dynamodb_table(table_name: str | None = None) -> Any:
    """Return the single-table DynamoDB Table resource."""
    target_name = table_name or os.environ.get("DYNAMODB_TABLE_NAME", "vgc-telemetry-dev")
    if local_mode():
        validate_local()
        if target_name != DEMO_NAME:
            raise ValueError("Local demo forbids non-demo tables")
    dynamodb = get_dynamodb_resource()
    return dynamodb.Table(target_name)


def batch_write_with_retry(
    table: Any,
    items: list[dict[str, Any]],
    batch_size: int = 25,
    max_retries: int = 5,
    base_backoff_sec: float = 0.05,
    *,
    sleep=time.sleep,
    random=randomness.random,
) -> None:
    """Write items in chunks of up to 25 items with backoff on UnprocessedItems.

    DynamoDB has a hard limit of 25 items per BatchWriteItem call.
    """
    if not 1 <= batch_size <= 25 or max_retries < 1:
        raise ValueError("Invalid batch size or retry count")
    for i in range(0, len(items), batch_size):
        chunk = items[i : i + batch_size]
        request_items = {table.name: [{"PutRequest": {"Item": item}} for item in chunk]}

        client = table.meta.client
        retries = 0
        while request_items and retries < max_retries:
            response = retry(
                lambda: client.batch_write_item(RequestItems=request_items),
                sleep=sleep,
                random=random,
            )
            unprocessed = response.get("UnprocessedItems", {})
            if not unprocessed or not unprocessed.get(table.name):
                break

            request_items = unprocessed
            retries += 1
            sleep_time = base_backoff_sec * (2 ** (retries - 1))
            if retries < max_retries:
                sleep(sleep_time * (1 + random()))

        if request_items and unprocessed.get(table.name):
            raise WriteExhausted(
                f"Failed to write all items via BatchWriteItem after {max_retries} retries: "
                f"{len(unprocessed[table.name])} items remained unprocessed."
            )
