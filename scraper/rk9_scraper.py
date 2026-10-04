import logging
import re
import time
import urllib.robotparser
from datetime import datetime, timezone
from urllib.parse import quote

import httpx

from core.models.champions import SourceCapture
from scraper.html import public_team_links
from scraper.http import captured_document, request_public

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
        public_scraping_authorized: bool = False,
        max_team_pages: int = 1000,
    ):
        self.base_url = base_url.rstrip("/")
        self.crawl_delay = crawl_delay
        self.public_scraping_authorized = public_scraping_authorized
        self.max_team_pages = max_team_pages
        self._robots_response = None
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
            robots_url = f"{self.base_url}/robots.txt"
            resp = request_public(self.client, robots_url)
            self._robots_response = resp
            if resp.status_code == 200:
                self._robot_parser.parse(resp.text.splitlines())
            else:
                self._robot_parser.disallow_all = True

        return self._robot_parser.can_fetch(self.user_agent, f"{self.base_url}/{path.lstrip('/')}")

    def _response(self, path: str):
        permitted = self._check_robots(path)
        if not permitted and not self.public_scraping_authorized:
            raise PermissionError(f"robots.txt disallows scraping path: {path}")
        if not permitted:
            logger.warning("Explicit operator policy overrides robots exclusion for %s", path)

        def throttle():
            elapsed = time.monotonic() - self._last_request_time
            if elapsed < self.crawl_delay:
                time.sleep(self.crawl_delay - elapsed)
            self._last_request_time = time.monotonic()

        return request_public(
            self.client, f"{self.base_url}/{path.lstrip('/')}", before_request=throttle
        )

    def _fetch_html(self, path: str) -> str:
        response = self._response(path)
        response.raise_for_status()
        return response.text

    def capture_tournament(self, tournament_id: str) -> SourceCapture:
        event_id = quote(tournament_id, safe="")
        documents = {
            key: captured_document(self._response(f"{path}/{event_id}"))
            for key, path in [
                ("event", "tournament"),
                ("roster", "roster"),
                ("pairings", "pairings"),
            ]
        }
        if self._robots_response is not None:
            documents["robots"] = captured_document(self._robots_response)
        if documents["roster"].status == 200:
            links = public_team_links(documents["roster"].body, self.base_url, tournament_id)
            for entry, url in list(links.items())[: self.max_team_pages]:
                documents["team:" + entry] = captured_document(
                    self._response(url[len(self.base_url) + 1 :])
                )
        return SourceCapture(
            source="rk9",
            source_event_id=tournament_id,
            fetched_at=datetime.now(timezone.utc),
            documents=documents,
        )

    def discover_recent_events(self) -> list[dict[str, str]]:
        """Open Item 2: Scrape RK9 event listing page to discover event IDs and dates."""
        html = self._fetch_html("events/pokemon")
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
