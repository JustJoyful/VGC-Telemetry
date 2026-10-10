"""Runtime read configuration; no secrets generated and no deployment changes."""

import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.v2.errors import unavailable


@dataclass(frozen=True)
class V2ReadConfig:
    secret: bytes | None = None
    default_catalog_revision: str = "champions-mc-3661ce4"
    clock: Callable[[], int] = lambda: int(time.time())
    listing_queries: int = 20
    candidate_page_size: int = 100
    listing_rows: int = 2000
    analytics_queries: int = 50
    analytics_rows: int = 5000
    analytics_days: int = 90
    response_bytes: int = 350_000
    token_seconds: int = 86400
    catalog_runtime: Any = None

    def __post_init__(self):
        if self.secret is not None and (
            not isinstance(self.secret, bytes) or len(self.secret) < 32
        ):
            raise ValueError("Cursor secret must be at least 32 bytes")

    def require_secret(self) -> bytes:
        if self.secret is None:
            raise unavailable("Cursor configuration is unavailable")
        return self.secret


def get_v2_config() -> V2ReadConfig:
    secret = os.environ.get("V2_CURSOR_SECRET")
    try:
        runtime = None
        if os.environ.get("CATALOG_RUNTIME_ENABLED") == "true":
            from app.v2.catalog import runtime_catalog

            runtime = runtime_catalog(os.environ["S3_RAW_BUCKET_NAME"])
        return V2ReadConfig(
            secret=secret.encode() if secret else None,
            default_catalog_revision=os.environ.get(
                "V2_DEFAULT_CATALOG_REVISION", "champions-mc-3661ce4"
            ),
            catalog_runtime=runtime,
        )
    except ValueError as error:
        raise unavailable("Cursor configuration is unavailable") from error
