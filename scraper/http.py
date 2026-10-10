"""Bounded public HTTP requests shared by source adapters."""

import hashlib
import math
import time

import httpx

from core.models.champions import CapturedDocument


def request_public(client, url, *, params=None, before_request=None, on_retry=None):
    for attempt in range(3):
        if before_request:
            before_request()
        try:
            response = client.get(url, params=params, timeout=15.0, follow_redirects=False)
        except httpx.TimeoutException:
            if on_retry:
                on_retry(attempt + 1, attempt == 2)
            if attempt == 2:
                raise
            time.sleep(min(2**attempt, 30))
            continue
        if response.status_code not in (429, 502, 503, 504):
            return response
        if on_retry:
            on_retry(attempt + 1, attempt == 2)
        if attempt == 2:
            response.raise_for_status()
        try:
            delay = float(response.headers.get("Retry-After", 2**attempt))
            if not math.isfinite(delay) or delay < 0:
                raise ValueError("Invalid retry delay")
        except ValueError:
            delay = 2**attempt
        time.sleep(min(delay, 30))
    raise RuntimeError("Exhausted source request")


def captured_document(response):
    # Champions public fixtures use UTF-8. Fail on undecodable bodies rather than alter evidence.
    body = response.content.decode("utf-8")
    return CapturedDocument(
        url=str(response.url),
        content_type=response.headers.get("content-type", ""),
        body=body,
        content_hash=hashlib.sha256(response.content).hexdigest(),
        status=response.status_code,
    )
