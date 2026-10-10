"""Bounded operational retries; programming and validation faults remain visible."""

import random as randomness
import time
from collections.abc import Callable
from typing import TypeVar

from botocore.exceptions import BotoCoreError, ClientError

T = TypeVar("T")
RETRYABLE = {
    "SlowDown",
    "Throttling",
    "ThrottlingException",
    "RequestLimitExceeded",
    "ProvisionedThroughputExceededException",
    "InternalServerError",
    "ServiceUnavailable",
    "RequestTimeout",
    "TransactionConflictException",
}


class BatchFailure(RuntimeError):
    def __init__(self, results: list[dict]):
        self.results = results
        super().__init__("Operational batch failure")


class WriteExhausted(RuntimeError):
    pass


def retry(
    operation: Callable[[], T], *, sleep=time.sleep, random=randomness.random, on_retry=None
) -> T:
    for attempt in range(3):
        try:
            return operation()
        except (ClientError, BotoCoreError) as error:
            if isinstance(error, ClientError):
                code = error.response["Error"]["Code"]
                status = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)
                if code not in RETRYABLE and status < 500:
                    raise
            if on_retry is not None:
                on_retry(attempt + 1, attempt == 2)
            if attempt == 2:
                raise
            sleep(0.05 * 2**attempt * (1 + random()))
    raise AssertionError("unreachable")
