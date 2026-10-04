"""EventBridge entry point for default-disabled Phase 0 source staging."""

import logging
import os
from pathlib import Path
from typing import Any

import boto3

from core.source_admission import load_admissions
from scraper.dispatcher import CollectionFailure, collect_sources

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]


def lambda_handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    admissions = load_admissions(
        Path(os.environ.get("SOURCE_ADMISSION_PATH", str(ROOT / "core/sources/admission.json")))
    )
    try:
        report = collect_sources(
            admissions=admissions,
            catalog_index=Path(
                os.environ.get("CATALOG_INDEX_PATH", str(ROOT / "core/regulations/v2/index.json"))
            ),
            s3_client=boto3.client("s3"),
            bucket_name=os.environ.get("S3_RAW_BUCKET_NAME", "vgc-telemetry-raw-dev"),
        )
    except CollectionFailure as error:
        logger.error("Collection failure report: %s", error.report.model_dump_json())
        raise
    return {"status": "success", "report": report.model_dump(mode="json")}
