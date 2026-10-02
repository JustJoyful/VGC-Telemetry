import logging
import re
import time
import urllib.robotparser
from typing import Any

import httpx

logger = logging.getLogger(__name__)

RK9_BASE_URL = "https://rk9.gg"


class RK9HTMLScraper:
    """HTML Scraper for RK9 tournament platform.

    Implements robots.txt checking, request rate limiting, and event discovery.
    """

    def __init__(
        self,
        base_url: str = RK9_BASE_URL,
        client: httpx.Client | None = None,
        crawl_delay: float = 1.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.crawl_delay = crawl_delay
        self.user_agent = "VGCTelemetryBot/1.0 (+https://github.com/JustJoyful/VGC-Telemetry)"
        self.client = client or httpx.Client(
            timeout=15.0,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        self._last_request_time = 0.0
        self._robot_parser: urllib.robotparser.RobotFileParser | None = None

    def _check_robots(self, path: str) -> bool:
        """Check robots.txt compliance before fetching path."""
        if self._robot_parser is None:
            self._robot_parser = urllib.robotparser.RobotFileParser()
            try:
                robots_url = f"{self.base_url}/robots.txt"
                resp = self.client.get(robots_url)
                if resp.status_code == 200:
                    self._robot_parser.parse(resp.text.splitlines())
                else:
                    self._robot_parser.allow_all = True
            except Exception as e:
                logger.warning("Could not fetch robots.txt (%s); proceeding with caution.", e)
                self._robot_parser.allow_all = True

        return self._robot_parser.can_fetch(self.user_agent, f"{self.base_url}/{path.lstrip('/')}")

    def _fetch_html(self, path: str) -> str:
        if not self._check_robots(path):
            raise PermissionError(f"robots.txt disallows scraping path: {path}")

        # Enforce rate limit / crawl delay
        elapsed = time.time() - self._last_request_time
        if elapsed < self.crawl_delay:
            time.sleep(self.crawl_delay - elapsed)

        url = f"{self.base_url}/{path.lstrip('/')}"
        response = self.client.get(url)
        self._last_request_time = time.time()
        response.raise_for_status()
        return response.text

    def discover_recent_events(self) -> list[dict[str, str]]:
        """Open Item 2: Scrape RK9 event listing page to discover event IDs and dates."""
        html = self._fetch_html("events")
        events: list[dict[str, str]] = []

        # Find tournament links e.g. href="/tournament/T123" or href="/roster/T123"
        pattern = re.compile(
            r'href=["\']/(?:tournament|roster|events)/([A-Za-z0-9_-]+)["\'][^>]*>([^<]+)<'
        )
        for match in pattern.finditer(html):
            tid, name = match.group(1), match.group(2).strip()
            if name and not any(e["tid"] == tid for e in events):
                events.append({"tid": tid, "name": name})

        return events

    def scrape_tournament_roster(self, tournament_id: str) -> list[dict[str, Any]]:
        """Scrape team rosters from RK9 roster table."""
        html = self._fetch_html(f"roster/{tournament_id}")

        # Extract table rows
        teams: list[dict[str, Any]] = []
        # Basic regex parser for structured table cells
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.DOTALL)
        standing = 1
        for row in rows:
            cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
            if len(cells) >= 2:
                player_name = re.sub(r"<[^>]+>", "", cells[0]).strip()
                if player_name:
                    teams.append(
                        {
                            "player_id": f"rk9_{tournament_id}_{standing}",
                            "player_name": player_name,
                            "standing": standing,
                            "record": "0-0",
                            "pokemon": [],
                        }
                    )
                    standing += 1

        return teams
