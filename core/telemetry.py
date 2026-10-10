"""Bounded CloudWatch metrics and redacted worker outcome logs."""

import json
import logging
import os
from collections import Counter
from uuid import uuid4

import boto3

NAMESPACE = "VGCTelemetry/Automation"
COUNTS = {
    "DiscoveryPages",
    "DiscoveredEvents",
    "Captures",
    "NormalizedRevisions",
    "AcceptedWork",
    "PendingWork",
    "RejectedWork",
    "CatalogCandidates",
    "CatalogPending",
    "CatalogActivations",
    "BuildLinks",
    "BuildConflicts",
    "Quarantines",
    "RetryAttempts",
    "RetryExhaustions",
    "WorkerErrors",
    "Publications",
}
AGES = {"TournamentCheckAge", "ReferenceCheckAge", "PublicationLag"}
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class RunTelemetry:
    def __init__(self, worker, *, source="limitless", context=None, client=None):
        self.worker = worker
        self.source = source
        self.run_id = getattr(context, "aws_request_id", None) or uuid4().hex
        self.environment = os.environ.get("ENVIRONMENT", "dev")
        self.client = client
        self.counts = Counter()

    def _client(self):
        if self.client is None:
            self.client = boto3.client("cloudwatch")
        return self.client

    def emit(self, name, value=1):
        if (
            name not in COUNTS | AGES
            or isinstance(value, bool)
            or not isinstance(value, (int, float))
        ):
            raise ValueError("Invalid automation metric")
        if name in COUNTS:
            self.counts[name] += value
        try:
            self._client().put_metric_data(
                Namespace=NAMESPACE,
                MetricData=[
                    {
                        "MetricName": name,
                        "Value": value,
                        "Unit": "Seconds" if name in AGES else "Count",
                        "Dimensions": [
                            {"Name": "Environment", "Value": self.environment},
                            {"Name": "Source", "Value": self.source},
                            {"Name": "Worker", "Value": self.worker},
                        ],
                    }
                ],
            )
        except Exception:
            logger.warning("metric_delivery_failed %s", name)

    def failure(self, error, *, event_ref=None, retry_count=0, result="error"):
        self.emit("WorkerErrors")
        record = {
            "run_id": self.run_id,
            "worker": self.worker,
            "source": self.source,
            "error_class": type(error).__name__,
            "retry_count": retry_count,
            "result": result,
        }
        if event_ref is not None:
            record["event_ref"] = event_ref
        logger.error(json.dumps(record, sort_keys=True))

    def finish(self, result):
        logger.info(
            json.dumps(
                {
                    "run_id": self.run_id,
                    "worker": self.worker,
                    "source": self.source,
                    "result": result,
                    "counts": dict(self.counts),
                },
                sort_keys=True,
            )
        )

    def retry(self, attempt, exhausted):
        self.emit("RetryAttempts")
        if exhausted:
            self.emit("RetryExhaustions")
            logger.error(
                json.dumps(
                    {
                        "run_id": self.run_id,
                        "worker": self.worker,
                        "source": self.source,
                        "error_class": "RetryExhausted",
                        "retry_count": attempt,
                        "result": "exhausted",
                    },
                    sort_keys=True,
                )
            )
