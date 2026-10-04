import json
import logging
import urllib.parse
from typing import Any

import boto3
from pydantic import ValidationError

from db.client import get_dynamodb_table
from ingestion.parser import parse_tournament_export
from ingestion.quarantine import quarantine_payload
from ingestion.writer import write_tournament_data

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def handle_s3_event(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """Lambda entry point triggered by S3 ObjectCreated events.

    Validates incoming tournament export against Pydantic contract.
    Quarantines corrupt/invalid payloads and ignores quarantine/ prefix defensively.
    """
    s3_client = boto3.client("s3")
    table = None

    records = event.get("Records", [])
    if not records:
        return {"status": "no_records"}

    results = []
    for record in records:
        bucket = record["s3"]["bucket"]["name"]
        raw_key = record["s3"]["object"]["key"]
        key = urllib.parse.unquote_plus(raw_key)

        # Defensive check against recursive trigger loops
        if key.startswith("quarantine/"):
            logger.warning("Ignoring event for quarantine prefix key: %s", key)
            return {"status": "skipped", "reason": "quarantine_prefix_ignored", "key": key}

        if not key.startswith("raw/"):
            results.append({"status": "skipped", "reason": "non_legacy_prefix", "key": key})
            continue

        try:
            response = s3_client.get_object(Bucket=bucket, Key=key)
            raw_bytes = response["Body"].read()
        except Exception as e:
            logger.error("Failed to read S3 object %s/%s: %s", bucket, key, e)
            return {"status": "error", "reason": "s3_read_failed", "error": str(e)}

        try:
            payload = json.loads(raw_bytes.decode("utf-8"))
            if isinstance(payload, dict) and "schema_version" in payload:
                results.append({"status": "skipped", "reason": "non_legacy_schema", "key": key})
                continue
            parsed = parse_tournament_export(payload)
        except (json.JSONDecodeError, ValidationError, Exception) as e:
            logger.warning("Payload in %s failed contract validation: %s. Quarantining.", key, e)
            quarantine_key = quarantine_payload(
                s3_client=s3_client,
                bucket_name=bucket,
                source_key=key,
                raw_content=raw_bytes,
                error_message=str(e),
            )
            return {
                "status": "quarantined",
                "key": key,
                "quarantine_key": quarantine_key,
                "error": str(e),
            }

        if table is None:
            table = get_dynamodb_table()
        success = write_tournament_data(table, parsed)
        results.append(
            {
                "status": "processed" if success else "skipped_duplicate",
                "key": key,
                "tid": parsed.export.metadata.tid,
            }
        )

    if len(results) == 1 and results[0].get("reason"):
        return results[0]
    return {"status": "completed", "results": results}


def lambda_handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    return handle_s3_event(event, context)
