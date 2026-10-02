import json
import logging
import os
from typing import Any

import boto3

from scraper.limitless_client import LimitlessAPIClient

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def fetch_and_store_tournaments(
    s3_client: Any,
    bucket_name: str,
    api_client: LimitlessAPIClient | None = None,
    game: str = "VGC",
    limit: int = 10,
) -> list[str]:
    """Fetch new tournament exports and write to S3 strictly under raw/ prefix."""
    client = api_client or LimitlessAPIClient()
    written_keys: list[str] = []

    try:
        tournaments = client.get_tournaments(game=game, limit=limit)
    except Exception as e:
        logger.error("Failed to fetch tournament list: %s", e)
        return []

    for t in tournaments:
        tid = t.get("id") or t.get("tid")
        if not tid:
            continue

        raw_key = f"raw/{tid}.json"

        # Check if already fetched
        try:
            s3_client.head_object(Bucket=bucket_name, Key=raw_key)
            logger.info("Tournament %s already exists in S3 at %s. Skipping.", tid, raw_key)
            continue
        except Exception:
            pass  # Object does not exist, proceed to fetch

        try:
            export_payload = client.fetch_tournament_export(tid)
            s3_client.put_object(
                Bucket=bucket_name,
                Key=raw_key,
                Body=json.dumps(export_payload, indent=2).encode("utf-8"),
                ContentType="application/json",
            )
            logger.info("Successfully fetched and wrote tournament %s to %s", tid, raw_key)
            written_keys.append(raw_key)
        except Exception as e:
            logger.error("Failed to fetch/store export for tournament %s: %s", tid, e)

    return written_keys


def lambda_handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """EventBridge Scheduler entry point."""
    bucket_name = os.environ.get("S3_RAW_BUCKET_NAME", "vgc-telemetry-raw-dev")
    s3_client = boto3.client("s3")
    written = fetch_and_store_tournaments(s3_client, bucket_name)
    return {"status": "success", "written_keys": written}
