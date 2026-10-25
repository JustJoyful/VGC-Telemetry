import logging
import re
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx

from core.models.champions import SourceCapture
from scraper.http import captured_document, request_public

logger = logging.getLogger(__name__)

LIMITLESS_BASE_URL = "https://play.limitlesstcg.com/api"


class RateLimitPacer:
    """Waits out the advertised ``RateLimit`` window instead of provoking 429 refusals.

    Limitless sends ``ratelimit: "50-in-5min"; r=<remaining>; t=<reset seconds>``.
    When the remaining quota reaches the reserve, the next request waits for the reset.
    """

    def __init__(self, *, reserve=1, max_wait=330.0, sleep=time.sleep, clock=time.monotonic):
        self.reserve = reserve
        self.max_wait = max_wait
        self.sleep = sleep
        self.clock = clock
        self.resume_at = 0.0

    def wait(self):
        delay = self.resume_at - self.clock()
        if delay > 0:
            logger.info("source_rate_limit_wait seconds=%.0f", delay)
            self.sleep(delay)

    def observe(self, response):
        header = response.headers.get("ratelimit", "")
        remaining = re.search(r"(?:^|[;\s])r=(\d+)", header)
        reset = re.search(r"(?:^|[;\s])t=(\d+)", header)
        if remaining is None or reset is None or int(remaining.group(1)) > self.reserve:
            return
        delay = min(float(reset.group(1)), self.max_wait) + 1
        self.resume_at = max(self.resume_at, self.clock() + delay)


# One process-wide pacer: discovery and worker clients share the same upstream quota.
SHARED_PACER = RateLimitPacer()


class LimitlessAPIClient:
    """REST API Client for Limitless Tournament Platform (play.limitlesstcg.com/api).

    Uses documented public endpoints with response header rate-limit handling.
    No API key required for tournament standings and pairings.
    """

    def __init__(
        self,
        base_url: str = LIMITLESS_BASE_URL,
        client: httpx.Client | None = None,
        on_retry=None,
        pacer: RateLimitPacer | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.on_retry = on_retry
        self.pacer = pacer or SHARED_PACER
        self.client = client or httpx.Client(
            timeout=15.0,
            headers={
                "User-Agent": "VGCTelemetryBot/1.0 (+https://github.com/JustJoyful/VGC-Telemetry)",
                "Accept": "application/json",
            },
        )

    def _response(self, endpoint: str, params: dict[str, Any] | None = None):
        return request_public(
            self.client,
            f"{self.base_url}/{endpoint.lstrip('/')}",
            params=params,
            before_request=self.pacer.wait,
            on_retry=self.on_retry,
            on_response=self.pacer.observe,
        )

    def _request(self, endpoint: str, params: dict[str, Any] | None = None) -> Any:
        response = self._response(endpoint, params)
        response.raise_for_status()
        return response.json()

    def get_games(self) -> list[dict[str, Any]]:
        return self._request("games")

    def capture_tournament(self, tournament_id: str) -> SourceCapture:
        documents = {
            key: captured_document(
                self._response(f"tournaments/{quote(tournament_id, safe='')}/{key}")
            )
            for key in ("details", "standings", "pairings")
        }
        return SourceCapture(
            source="limitless",
            source_event_id=tournament_id,
            fetched_at=datetime.now(timezone.utc),
            documents=documents,
        )

    def get_tournaments(
        self, game: str = "VGC", format: str | None = None, limit: int = 50, page: int | None = None
    ) -> list[dict[str, Any]]:
        """List tournaments filtered by game (e.g. VGC) and format."""
        if not 1 <= limit <= 100 or (page is not None and page < 1):
            raise ValueError("Invalid Limitless page or limit")
        params: dict[str, Any] = {"game": game, "limit": limit}
        if page is not None:
            params["page"] = page
        if format:
            params["format"] = format
        return self._request("tournaments", params=params)

    def get_tournament_details(self, tournament_id: str) -> dict[str, Any]:
        """Fetch details for a single tournament."""
        return self._request(f"tournaments/{quote(tournament_id, safe='')}/details")

    def get_standings(self, tournament_id: str) -> list[dict[str, Any]]:
        """Fetch tournament standings including team decklists/rosters."""
        return self._request(f"tournaments/{tournament_id}/standings")

    def get_pairings(self, tournament_id: str) -> list[dict[str, Any]]:
        """Fetch tournament match pairings and results."""
        return self._request(f"tournaments/{tournament_id}/pairings")
