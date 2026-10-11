"""Reproducible local measurements; these are never AWS capacity/latency measurements."""

import argparse
import base64
import hashlib
import importlib.metadata
import json
import math
import platform
import resource
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import boto3
import httpx
from boto3.dynamodb.types import TypeSerializer
from pydantic import BaseModel, Field

from core.aws import DEMO_NAME, aws_client, validate_local
from db.client import get_dynamodb_table
from scripts.local import demo
from scripts.local.storage import reset_demo


def source_identity(root):
    paths = sorted(
        p
        for folder in ("app", "core", "db", "ingestion", "scraper", "scripts", "local")
        for p in (root / folder).rglob("*")
        if p.is_file() and p.suffix in {".py", ".json", ".example"}
    ) + [
        root / p
        for p in (
            "Dockerfile.local",
            "Dockerfile.local.dockerignore",
            ".dockerignore",
            "compose.yaml",
            "uv.lock",
            "pyproject.toml",
        )
    ]
    digest = hashlib.sha256()
    for path in paths:
        digest.update(str(path.relative_to(root)).encode() + b"\0" + path.read_bytes())
    inputs = {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for folder in ("tests/fixtures", "core/regulations/v2")
        for p in sorted((root / folder).rglob("*"))
        if p.is_file()
    }
    return digest.hexdigest(), inputs


def verify_identity(provenance, identity):
    if identity != (provenance["source_tree_sha256"], provenance["inputs"]):
        raise ValueError(
            "Executed source/input identity differs from host provenance; rebuild stack"
        )


class BenchmarkReport(BaseModel):
    schema_version: int = 1
    measured_at: str
    revision: str
    source_tree_sha256: str
    command: list[str]
    inputs: dict[str, str]
    environment: dict
    dataset: dict
    timings: dict
    operations: dict
    items: dict
    limitations: list[str] = Field(min_length=1)


def attribute_size(value):
    kind, content = next(iter(value.items()))
    if kind == "S":
        return len(content.encode())
    if kind == "N":
        digits = "".join(str(d) for d in Decimal(content).as_tuple().digits).strip("0")
        return math.ceil(max(1, len(digits)) / 2) + 1
    if kind == "B":
        return len(content)
    if kind in {"BOOL", "NULL"}:
        return 1
    if kind == "L":
        return 3 + sum(attribute_size(v) + 1 for v in content)
    if kind == "M":
        return 3 + sum(len(k.encode()) + attribute_size(v) + 1 for k, v in content.items())
    if kind in {"SS", "NS", "BS"}:
        return sum(attribute_size({kind[0]: v}) for v in content)
    raise ValueError("Unknown DynamoDB attribute type")


def item_sizes(row):
    encoded = {k: TypeSerializer().serialize(v) for k, v in row.items()}
    stored = sum(len(k.encode()) + attribute_size(v) for k, v in encoded.items())
    wire = json.dumps(
        encoded,
        separators=(",", ":"),
        ensure_ascii=False,
        default=lambda v: base64.b64encode(bytes(v)).decode(),
    ).encode()
    return {
        "stored_attribute_bytes": stored,
        "wire_json_bytes": len(wire),
        "storage_overhead_bytes": 100,
        "estimated_single_write_units": math.ceil(stored / 1024),
        "estimated_strong_read_units": math.ceil(stored / 4096),
    }


def distribution(values):
    ordered = sorted(values)
    if not ordered:
        return {"count": 0}
    return {
        "count": len(values),
        "min": ordered[0],
        "median": statistics.median(values),
        "p95": ordered[math.ceil(0.95 * len(ordered)) - 1],
        "max": ordered[-1],
        "total": sum(values),
    }


def scan_items(table):
    rows, cursor = [], None
    while True:
        args = {"ConsistentRead": True}
        if cursor:
            args["ExclusiveStartKey"] = cursor
        page = table.scan(**args)
        rows.extend(page["Items"])
        cursor = page.get("LastEvaluatedKey")
        if not cursor:
            return rows


def benchmark(*, repeats, api_url, provenance):
    validate_local()
    verify_identity(provenance, source_identity(Path("/work")))
    phases = defaultdict(list)
    operations = Counter()
    transactions = Counter()
    started = time.perf_counter()
    rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    table, s3 = get_dynamodb_table(), aws_client("s3")

    def observe(model, params, **kwargs):
        operations[f"{model.service_model.service_name}.{model.name}"] += 1
        if model.name == "TransactWriteItems":
            for entry in params["TransactItems"]:
                transactions.update(entry.keys())

    boto3.DEFAULT_SESSION.events.register("before-parameter-build.*.*", observe)
    for client in (table.meta.client, s3):
        client.meta.events.register("before-parameter-build.*.*", observe)
    originals = {
        name: getattr(demo, name)
        for name in ("collect", "ingest", "recover", "publication", "rollback")
    }
    ingest_names = iter(
        ["native_ingestion", "duplicate_ingestion", "correction_ingestion", "replay_ingestion"]
        * (repeats + 1)
    )
    recover_names = iter(
        [
            "complete_snapshot_build",
            "duplicate_recovery",
            "interrupted_recovery",
            "rollback_replay_build",
        ]
        * (repeats + 1)
    )

    def measured(name, fn):
        def wrapped(*args, **kwargs):
            label = (
                next(ingest_names)
                if name == "ingest"
                else (next(recover_names) if name == "recover" else name)
            )
            if name == "collect":
                label = "collection_normalization_" + (
                    "synthetic_correction"
                    if kwargs.get("correction")
                    else "representative"
                    if kwargs.get("second")
                    else "largest_retained"
                )
            start = time.perf_counter()
            try:
                return fn(*args, **kwargs)
            finally:
                phases[label].append(time.perf_counter() - start)

        return wrapped

    for name, fn in originals.items():
        setattr(demo, name, measured(name, fn))
    reports = []
    try:
        for _ in range(repeats + 1):
            reset_demo(confirm=True)
            reports.append(demo.demonstrate(table, s3, DEMO_NAME))
    finally:
        for name, fn in originals.items():
            setattr(demo, name, fn)
    paths = {
        "api_representative": "/v2/meta?metric=species&limit=25",
        "api_max_window_and_page": (
            "/v2/cores?size=3&date_from=2026-07-08&date_to=2026-10-05&limit=100"
        ),
        "api_max_listing_page": "/v2/teams?eligibility=all&limit=100",
    }
    with httpx.Client(base_url=api_url, trust_env=False, timeout=120) as client:
        for name, path in paths.items():
            for _ in range(repeats + 1):
                start = time.perf_counter()
                response = client.get(path)
                phases[name].append(time.perf_counter() - start)
                assert response.status_code == 200, (
                    path,
                    response.status_code,
                    response.text[:200],
                )
    rows = scan_items(table)
    sizes = [item_sizes(row) for row in rows]
    indices = {}
    for index in ("GSI1", "GSI2"):
        projected = [
            size["stored_attribute_bytes"]
            for row, size in zip(rows, sizes)
            if index + "PK" in row and index + "SK" in row
        ]
        indices[index] = {"projection": "ALL", "attribute_bytes": distribution(projected)}
    largest = sorted(
        zip(rows, sizes), key=lambda pair: pair[1]["stored_attribute_bytes"], reverse=True
    )[:5]
    report = BenchmarkReport(
        measured_at=datetime.now(UTC).isoformat(),
        revision=provenance["revision"],
        source_tree_sha256=provenance["source_tree_sha256"],
        command=provenance["command"],
        inputs=provenance["inputs"],
        environment=provenance["environment"]
        | {
            "python": sys.version,
            "uv": provenance["environment"].get("uv"),
            "container_os": platform.platform(),
            "architecture": platform.machine(),
            "dependencies": {
                p: importlib.metadata.version(p)
                for p in ("boto3", "botocore", "fastapi", "pydantic", "httpx", "uvicorn")
            },
            "memory_method": "Linux process cumulative ru_maxrss KiB; excludes child/server RSS",
            "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "prior_peak_rss_kib": rss_before,
        },
        dataset={
            "retained_events": 2,
            "context_rosters": 37,
            "largest_event_rosters": 31,
            "baseline_eligible_rosters": 10,
            "corrected_eligible_rosters": 9,
            "maximum_roster_slots": 6,
            "maximum_moves_per_slot": 4,
            "query_window_days": 90,
            "query_page_limit": 100,
            "core_size": 3,
            "repeated_invocations": repeats,
            "persisted_items": len(rows),
            "semantic_results_equal": all(r["replayed"] == reports[0]["replayed"] for r in reports),
        },
        timings={
            name: {
                "first_seconds": vals[0],
                "repeated_seconds": distribution(vals[1:]),
                "samples_seconds": vals,
            }
            for name, vals in phases.items()
        }
        | {"driver_wall_seconds": time.perf_counter() - started},
        operations={
            "sdk_calls": dict(operations),
            "transaction_actions": dict(transactions),
            "scope": "pipeline and initializer DynamoDB/S3 clients; HTTP API server calls excluded",
        },
        items={
            "stored_attribute_bytes": distribution([s["stored_attribute_bytes"] for s in sizes]),
            "serialized_wire_json_bytes": distribution([s["wire_json_bytes"] for s in sizes]),
            "minimum_storage_overhead_bytes": 100 * len(rows),
            "gsi": indices,
            "largest": [
                {"key": {k: row[k] for k in ("PK", "SK")}, **size} for row, size in largest
            ],
        },
        limitations=[
            "DynamoDB Local ignores provisioned throughput; "
            "transactions/concurrency differ from AWS.",
            "First invocation is process/fixture cold; Docker/services/OS "
            "caches were already warm.",
            "Number storage bytes follow AWS's approximate significant-digit formula.",
            "Stored bytes, serialized wire bytes, storage overhead and "
            "estimated units are separate.",
            "Strong read/write estimates per item are not consumed/billed AWS "
            "units; transactions double units.",
            "GSI ALL projects whole items only where both index keys exist; "
            "not measured AWS amplification.",
            "API operation counts and API/server/storage RSS are excluded "
            "from driver instrumentation.",
            "Maximum legal query shapes on retained data do not saturate "
            "5000-row/50-query budgets.",
            "No finite global ingestion/event count bound exists; largest "
            "retained fixture is measured.",
            "Synthetic correction and publication interruption are local "
            "fault cases, not source evidence.",
            "No local result proves AWS latency, absence of throttling, cost, "
            "or Lambda/SAM memory.",
        ],
    )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provenance", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--api-url", default="http://api:8080")
    parser.add_argument("--confirm-reset", action="store_true")
    args = parser.parse_args()
    if not args.confirm_reset or not 2 <= args.repeats <= 10:
        parser.error("requires --confirm-reset and 2–10 repeats")
    report = benchmark(
        repeats=args.repeats,
        api_url=args.api_url,
        provenance=json.loads(args.provenance.read_text()),
    )
    print(report.model_dump_json())


if __name__ == "__main__":
    main()
