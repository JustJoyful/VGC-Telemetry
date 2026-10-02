import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

LIMITLESS_BASE_URL = "https://play.limitlesstcg.com/api"


class LimitlessAPIClient:
    """REST API Client for Limitless Tournament Platform (play.limitlesstcg.com/api).

    Uses documented public endpoints with response header rate-limit handling.
    No API key required for tournament standings and pairings.
    """

    def __init__(self, base_url: str = LIMITLESS_BASE_URL, client: httpx.Client | None = None):
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(
            timeout=15.0,
            headers={
                "User-Agent": "VGCTelemetryBot/1.0 (+https://github.com/JustJoyful/VGC-Telemetry)",
                "Accept": "application/json",
            },
        )

    def _request(self, endpoint: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        max_retries = 3

        for attempt in range(max_retries):
            response = self.client.get(url, params=params)

            # Handle documented rate limits
            if response.status_code == 429:
                retry_after = float(response.headers.get("Retry-After", 2.0))
                logger.warning("Limitless rate limited. Sleeping for %.1f seconds.", retry_after)
                time.sleep(retry_after)
                continue

            # Check header remaining
            remaining = response.headers.get("X-RateLimit-Remaining")
            if remaining is not None and int(remaining) <= 1:
                logger.debug("Rate limit budget near zero (%s). Throttling 1s.", remaining)
                time.sleep(1.0)

            response.raise_for_status()
            return response.json()

        raise RuntimeError(f"Exceeded max retries calling {url} due to rate limits.")

    def get_tournaments(
        self, game: str = "VGC", format: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        """List tournaments filtered by game (e.g. VGC) and format."""
        params: dict[str, Any] = {"game": game, "limit": limit}
        if format:
            params["format"] = format
        return self._request("tournaments", params=params)

    def get_tournament_details(self, tournament_id: str) -> dict[str, Any]:
        """Fetch details for a single tournament."""
        return self._request(f"tournaments/{tournament_id}")

    def get_standings(self, tournament_id: str) -> list[dict[str, Any]]:
        """Fetch tournament standings including team decklists/rosters."""
        return self._request(f"tournaments/{tournament_id}/standings")

    def get_pairings(self, tournament_id: str) -> list[dict[str, Any]]:
        """Fetch tournament match pairings and results."""
        return self._request(f"tournaments/{tournament_id}/pairings")

    def fetch_tournament_export(
        self, tournament_id: str, regulation: str = "M-C"
    ) -> dict[str, Any]:
        """Fetch and assemble raw tournament JSON export."""
        details = self.get_tournament_details(tournament_id)
        standings = self.get_standings(tournament_id)
        pairings = self.get_pairings(tournament_id)

        teams = []
        for s in standings:
            # Standings include player details and roster
            wins = s.get("record", {}).get("wins", 0)
            losses = s.get("record", {}).get("losses", 0)
            teams.append(
                {
                    "player_id": s.get("player", {}).get("id", s.get("id", "unknown")),
                    "player_name": s.get("player", {}).get("name", "Unknown Player"),
                    "standing": s.get("placing", 1),
                    "record": f"{wins}-{losses}",
                    "pokemon": s.get("decklist", s.get("pokemon", [])),
                }
            )

        matches = []
        for p in pairings:
            matches.append(
                {
                    "round": p.get("round", 1),
                    "match_id": str(p.get("id", p.get("match_id", ""))),
                    "player1_id": p.get("player1", {}).get("id", ""),
                    "player2_id": p.get("player2", {}).get("id", ""),
                    "winner_id": p.get("winner", {}).get("id", ""),
                    "score": p.get("score"),
                }
            )

        return {
            "metadata": {
                "tid": tournament_id,
                "name": details.get("name", f"Tournament {tournament_id}"),
                "date": details.get("date", time.strftime("%Y-%m-%d")),
                "regulation": regulation,
                "tier": details.get("tier", "Major"),
                "format": "Pokémon Champions VGC",
            },
            "teams": teams,
            "matches": matches,
        }
