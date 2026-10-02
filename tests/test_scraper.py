import json

import boto3
import httpx

from scraper.handler import fetch_and_store_tournaments
from scraper.limitless_client import LimitlessAPIClient
from scraper.rk9_scraper import RK9HTMLScraper


def test_limitless_client_fetch_and_assemble():
    def mock_transport(request: httpx.Request):
        url = str(request.url)
        if "tournaments/T999/standings" in url:
            return httpx.Response(
                200,
                json=[
                    {
                        "player": {"id": "P99", "name": "Test Player"},
                        "placing": 1,
                        "record": {"wins": 5, "losses": 0},
                        "decklist": [],
                    }
                ],
            )
        elif "tournaments/T999/pairings" in url:
            return httpx.Response(
                200,
                json=[
                    {
                        "round": 1,
                        "id": "M99",
                        "player1": {"id": "P99"},
                        "player2": {"id": "P100"},
                        "winner": {"id": "P99"},
                        "score": "2-0",
                    }
                ],
            )
        elif "tournaments/T999" in url:
            return httpx.Response(
                200,
                json={"name": "Mock Limitless Cup", "date": "2026-09-25", "tier": "Regional"},
            )
        elif "tournaments" in url:
            return httpx.Response(200, json=[{"id": "T999", "name": "Mock Limitless Cup"}])
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(mock_transport))
    limitless = LimitlessAPIClient(client=client)

    export = limitless.fetch_tournament_export("T999", regulation="M-C")
    assert export["metadata"]["tid"] == "T999"
    assert export["metadata"]["name"] == "Mock Limitless Cup"
    assert len(export["teams"]) == 1
    assert export["teams"][0]["player_id"] == "P99"
    assert len(export["matches"]) == 1


def test_rk9_scraper_discover_and_roster():
    def mock_transport(request: httpx.Request):
        url = str(request.url)
        if "robots.txt" in url:
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        elif "events" in url:
            html = """
            <html>
                <body>
                    <a href="/tournament/RK9_100">Champions Open 2026</a>
                </body>
            </html>
            """
            return httpx.Response(200, text=html)
        elif "roster/RK9_100" in url:
            html = """
            <table>
                <tr><td>Player One</td><td>Masters</td></tr>
                <tr><td>Player Two</td><td>Masters</td></tr>
            </table>
            """
            return httpx.Response(200, text=html)
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(mock_transport))
    scraper = RK9HTMLScraper(client=client, crawl_delay=0.0)

    events = scraper.discover_recent_events()
    assert len(events) == 1
    assert events[0]["tid"] == "RK9_100"

    roster = scraper.scrape_tournament_roster("RK9_100")
    assert len(roster) == 2
    assert roster[0]["player_name"] == "Player One"


def test_scraper_handler_writes_to_raw_prefix(s3_bucket):
    def mock_transport(request: httpx.Request):
        url = str(request.url)
        if "tournaments/T888/standings" in url:
            return httpx.Response(200, json=[])
        elif "tournaments/T888/pairings" in url:
            return httpx.Response(200, json=[])
        elif "tournaments/T888" in url:
            return httpx.Response(
                200,
                json={"name": "Auto Cup", "date": "2026-09-25", "tier": "Major"},
            )
        elif "tournaments" in url:
            return httpx.Response(200, json=[{"id": "T888", "name": "Auto Cup"}])
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(mock_transport))
    limitless = LimitlessAPIClient(client=client)

    s3_client = boto3.client("s3", region_name="us-east-1")
    written = fetch_and_store_tournaments(
        s3_client=s3_client,
        bucket_name=s3_bucket.name,
        api_client=limitless,
        limit=5,
    )

    assert len(written) == 1
    assert written[0] == "raw/T888.json"

    # Confirm object exists in S3 under raw/ prefix
    obj = s3_bucket.Object("raw/T888.json").get()
    data = json.loads(obj["Body"].read().decode("utf-8"))
    assert data["metadata"]["tid"] == "T888"
