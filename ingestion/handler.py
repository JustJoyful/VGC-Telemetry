import json
import logging
import random as randomness
import time
import urllib.parse
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from pydantic import ValidationError

from core.telemetry import RunTelemetry
from db.client import get_dynamodb_table
from ingestion.parser import parse_tournament_export
from ingestion.quarantine import quarantine_payload
from ingestion.retry import BatchFailure, WriteExhausted, retry
from ingestion.writer import write_tournament_data

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def handle_s3_event(
    event: dict[str, Any], context: Any = None, *, sleep=time.sleep, random=randomness.random
) -> dict[str, Any]:
    """Lambda entry point triggered by S3 ObjectCreated events.

    Validates incoming tournament export against Pydantic contract.
    Quarantines corrupt/invalid payloads and ignores quarantine/ prefix defensively.
    """
    telemetry = RunTelemetry("legacy", context=context)
    try:
        result = _handle_s3_event(event, sleep=sleep, random=random, telemetry=telemetry)
    except Exception as error:
        if not isinstance(error, BatchFailure):
            telemetry.failure(error)
        telemetry.finish("error")
        raise
    telemetry.finish(result["status"])
    return result


def _handle_s3_event(event, *, sleep, random, telemetry):
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
            logger.warning("Ignoring event for quarantine prefix")
            results.append({"status": "skipped", "reason": "quarantine_prefix_ignored", "key": key})
            continue

        if not key.startswith("raw/"):
            results.append({"status": "skipped", "reason": "non_legacy_prefix", "key": key})
            continue

        try:
            args = {"Bucket": bucket, "Key": key}
            if record["s3"]["object"].get("versionId"):
                args["VersionId"] = record["s3"]["object"]["versionId"]
            response = retry(
                lambda: s3_client.get_object(**args),
                sleep=sleep,
                random=random,
                on_retry=telemetry.retry,
            )
            raw_bytes = response["Body"].read()
        except (ClientError, BotoCoreError) as e:
            telemetry.failure(e, result="error")
            results.append({"status": "error", "reason": "s3_read_failed", "error": str(e)})
            continue

        try:
            payload = json.loads(raw_bytes.decode("utf-8"))
            if isinstance(payload, dict) and "schema_version" in payload:
                results.append({"status": "skipped", "reason": "non_legacy_schema", "key": key})
                continue
            parsed = parse_tournament_export(payload)
        except (json.JSONDecodeError, UnicodeDecodeError, ValidationError) as e:
            telemetry.failure(e, result="quarantine")
            validation_message = str(e)
            try:
                quarantine_key = retry(
                    lambda: quarantine_payload(
                        s3_client=s3_client,
                        bucket_name=bucket,
                        source_key=key,
                        raw_content=raw_bytes,
                        error_message=validation_message,
                    ),
                    sleep=sleep,
                    random=random,
                    on_retry=telemetry.retry,
                )
                telemetry.emit("Quarantines")
                results.append(
                    {
                        "status": "quarantined",
                        "key": key,
                        "quarantine_key": quarantine_key,
                        "error": str(e),
                    }
                )
            except (ClientError, BotoCoreError) as failure:
                telemetry.failure(failure)
                results.append({"status": "error", "key": key, "error": str(failure)})
            continue

        if table is None:
            table = get_dynamodb_table()
        try:
            success = retry(
                lambda: write_tournament_data(table, parsed),
                sleep=sleep,
                random=random,
                on_retry=telemetry.retry,
            )
        except (ClientError, BotoCoreError, WriteExhausted) as failure:
            telemetry.failure(failure)
            results.append({"status": "error", "key": key, "error": str(failure)})
            continue
        results.append(
            {
                "status": "processed" if success else "skipped_duplicate",
                "key": key,
                "tid": parsed.export.metadata.tid,
            }
        )
        if success:
            telemetry.emit("Publications")

    if any(r["status"] == "error" for r in results):
        raise BatchFailure(results)
    if len(results) == 1 and results[0].get("reason"):
        return results[0]
    return {"status": "completed", "results": results}


def lambda_handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    if event.get("mode") == "build":
        from ingestion.build_handler import handle_build_event

        return handle_build_event(event, context)
    if event.get("mode") == "native":
        from ingestion.native_handler import handle_native_event

        return handle_native_event(event, context)
    return handle_s3_event(event, context)
