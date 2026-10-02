import json
import os
from datetime import datetime, timezone
from typing import Any


def quarantine_payload(
    s3_client: Any,
    bucket_name: str,
    source_key: str,
    raw_content: bytes,
    error_message: str,
) -> str:
    """Quarantine malformed or schema-violating payload to S3 quarantine/ prefix.

    Isolates bad files with full diagnostic context while preventing S3 trigger loops.
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    basename = os.path.basename(source_key) or "payload.json"
    quarantine_key = f"quarantine/{timestamp}_{basename}"

    doc = {
        "error": error_message,
        "source_key": source_key,
        "timestamp": timestamp,
        "raw_payload": raw_content.decode("utf-8", errors="replace"),
    }

    s3_client.put_object(
        Bucket=bucket_name,
        Key=quarantine_key,
        Body=json.dumps(doc, indent=2).encode("utf-8"),
        ContentType="application/json",
    )

    return quarantine_key
