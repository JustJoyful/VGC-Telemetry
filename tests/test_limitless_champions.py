import hashlib
import importlib
import importlib.util
import json

import httpx
import pytest

from scraper.limitless_client import LimitlessAPIClient
from tests.source_helpers import real_inputs


def normalize(capture=None):
    assert importlib.util.find_spec("scraper.limitless_normalizer"), (
        "Captured normalization is missing"
    )
    original, admission, catalog = real_inputs("limitless")
    return importlib.import_module("scraper.limitless_normalizer").normalize_limitless_capture(
        capture or original, admission, catalog
    )


def changed(capture, key, value):
    body = json.dumps(value)
    doc = capture.documents[key].model_copy(
        update={"body": body, "content_hash": hashlib.sha256(body.encode()).hexdigest()}
    )
    return capture.model_copy(update={"documents": capture.documents | {key: doc}})


def test_documented_details_and_scalar_player_ids():
    paths = []
    client = LimitlessAPIClient(
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda r: paths.append(r.url.path) or httpx.Response(200, json={})
            )
        )
    )
    client.get_tournament_details("event")
    assert paths == ["/api/tournaments/event/details"]
    result = normalize()
    assert result.admission_status == "verified"
    team = result.tournament.teams[0]
    assert team.player_id == "limitless:azelle"
    assert team.record.wins == 8 and team.record.ties == 0
    assert len(team.pokemon) == 6
    mon = team.pokemon[0]
    assert mon.species_id == "floette" and mon.form_id == "floetteeternal"
    assert mon.item.item_id == "floettite" and mon.nature_id == "modest"
    assert mon.mega_variant_ids == ["floettemega"] and mon.stat_points is None
    # Last entrant's public sheet contains misspellings; retain species and diagnose them.
    assert {d.code for d in result.diagnostics} == {
        "illegal_ability",
        "unknown_nature",
        "illegal_item",
    }
    assert len(result.tournament.teams) == 31


def test_unpublished_values_and_dates_are_not_invented():
    capture, _, _ = real_inputs("limitless")
    details = json.loads(capture.documents["details"].body)
    details.pop("date")
    capture = changed(capture, "details", details)
    capture = changed(capture, "standings", [{"player": "public-id", "name": "Player"}])
    result = normalize(capture)
    assert result.tournament.metadata.starts_at is None
    assert result.tournament.teams[0].standing is None
    assert result.tournament.teams[0].record is None
    assert result.tournament.teams[0].publication_status == "absent"


def test_phase_scoped_matches_and_documented_outcomes():
    capture, _, _ = real_inputs("limitless")
    rows = [
        {"phase": 1, "round": 1, "table": 1, "player1": "a", "player2": "b", "winner": 0},
        {"phase": 2, "round": 1, "table": 1, "player1": "a", "player2": "b", "winner": -1},
        {"phase": 2, "round": 1, "match": "T2-1", "player1": "a", "player2": "b", "winner": "a"},
        {"phase": 3, "round": 1, "table": 1, "player1": "a", "winner": "a"},
        {"phase": 3, "round": 1, "table": 2, "player1": "a", "winner": -1},
        {"phase": 3, "round": 1, "table": 3, "player1": "a", "player2": "b"},
    ]
    result = normalize(changed(capture, "pairings", rows))
    assert len({m.match_id for m in result.tournament.matches}) == 6
    assert [m.outcome for m in result.tournament.matches] == [
        "tie",
        "double_loss",
        "win",
        "bye",
        "automatic_loss",
        "pending",
    ]
    assert result.tournament.matches[2].bracket_label == "T2-1"
    assert all(m.score is None for m in result.tournament.matches)


def test_request_registry_and_page_forwarding():
    urls = []
    client = LimitlessAPIClient(
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda r: urls.append(r.url) or httpx.Response(200, json=[])
            )
        )
    )
    assert hasattr(client, "get_games"), "Game registry request is missing"
    client.get_games()
    client.get_tournaments(format="M-C", page=2, limit=5)
    assert urls[0].path.endswith("/games")
    assert urls[1].params["page"] == "2" and "platform" not in urls[1].params


@pytest.mark.parametrize("status", [429, 502, 503, 504])
def test_bounded_retries_and_malformed_headers(status, monkeypatch):
    attempts = []
    delays = []
    monkeypatch.setattr("time.sleep", delays.append)
    client = LimitlessAPIClient(
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda r: (
                    attempts.append(r) or httpx.Response(status, headers={"Retry-After": "garbage"})
                )
            )
        )
    )
    with pytest.raises((RuntimeError, httpx.HTTPStatusError)):
        client.get_tournament_details("event")
    assert len(attempts) == 3 and len(delays) == 2 and all(0 <= d <= 30 for d in delays)


def test_timeout_and_access_refusals(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    for status in [None, 401, 403]:
        attempts = []

        def respond(request):
            attempts.append(request)
            if status is None:
                raise httpx.ReadTimeout("timeout", request=request)
            return httpx.Response(status)

        client = LimitlessAPIClient(client=httpx.Client(transport=httpx.MockTransport(respond)))
        with pytest.raises((httpx.HTTPError, RuntimeError)):
            client.get_tournament_details("event")
        assert len(attempts) == (3 if status is None else 1)
