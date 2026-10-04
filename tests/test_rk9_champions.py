import importlib
import importlib.util

import httpx
import pytest

from scraper.rk9_scraper import RK9HTMLScraper
from tests.source_helpers import real_inputs


def normalize(capture=None):
    assert importlib.util.find_spec("scraper.rk9_normalizer"), (
        "Actual public sheet normalization is missing"
    )
    original, admission, catalog = real_inputs("rk9")
    return importlib.import_module("scraper.rk9_normalizer").normalize_rk9_capture(
        capture or original, admission, catalog
    )


def replaced_body(capture, key, body, status=200):
    import hashlib

    doc = capture.documents[key].model_copy(
        update={
            "body": body,
            "content_hash": hashlib.sha256(body.encode()).hexdigest(),
            "status": status,
        }
    )
    return capture.model_copy(update={"documents": capture.documents | {key: doc}})


def test_real_public_sheets_and_divisions():
    result = normalize()
    assert result.admission_status == "verified"
    complete = [t for t in result.tournament.teams if t.publication_status == "complete"]
    assert len(complete) == 2
    team = next(t for t in complete if t.entry_id.endswith("003kOPkX8H90ISnOv3VG"))
    assert team.player_id is None and team.division_id == "masters"
    assert team.standing == 468 and len(team.pokemon) == 6
    assert team.pokemon[0].species_id == "sneasler"
    assert team.pokemon[0].nature_id == "adamant"
    assert team.pokemon[0].moves == ["direclaw", "closecombat", "fakeout", "coaching"]
    salamence = team.pokemon[3]
    assert salamence.species_id == "salamence" and salamence.ability_id == "intimidate"
    assert salamence.item.item_id == "salamencite" and salamence.mega_variant_ids == [
        "salamencemega"
    ]
    assert all(p.stat_points is None for t in complete for p in t.pokemon)
    assert any(d.code == "team_not_captured" for d in result.diagnostics)


def test_masked_player_ids_and_names_never_become_identities():
    c, _, _ = real_inputs("rk9")
    ids = ["003kOPkX8H90ISnOv3VG", "02c0M7gOmmfV1DOmYofh"]

    def roster(order):
        return (
            "<table><tr><th>Player ID</th><th>First Name</th><th>Last Name</th>"
            "<th>Country</th><th>Division</th><th>Trainer Name</th>"
            "<th>Team List</th><th>Standing</th></tr>"
            + "".join(
                "<tr><td>1....9</td><td>Same</td><td>Name</td><td>DE</td>"
                "<td>Masters</td><td>Trainer</td>"
                f'<td><a href="/teamlist/public/{c.source_event_id}/{i}">View</a></td>'
                f"<td>{n + 1}</td></tr>"
                for n, i in enumerate(order)
            )
            + "</table>"
        )

    a = normalize(replaced_body(c, "roster", roster(ids)))
    b = normalize(replaced_body(c, "roster", roster(ids[::-1])))
    assert {t.entry_id for t in a.tournament.teams} == {t.entry_id for t in b.tournament.teams}
    assert all(t.player_id is None for t in a.tournament.teams)


def test_participant_rows_are_not_public_pokemon_rosters():
    c, _, _ = real_inputs("rk9")
    r = normalize(
        replaced_body(
            c, "roster", "<table><tr><td>1....9</td><td>Name</td><td>Masters</td></tr></table>"
        )
    )
    assert r.tournament.teams == []
    assert any(
        d.code in ["missing_entry_identity", "unexpected_roster_markup"] for d in r.diagnostics
    )


def test_inaccessible_sheet_and_unexpected_markup_are_diagnosed():
    c, _, _ = real_inputs("rk9")
    key = "team:003kOPkX8H90ISnOv3VG"
    for body, status, code in [
        ("Forbidden", 403, "team_unavailable"),
        ("<html>changed markup</html>", 200, "unexpected_team_markup"),
    ]:
        result = normalize(replaced_body(c, key, body, status))
        team = next(
            t for t in result.tournament.teams if t.entry_id.endswith("003kOPkX8H90ISnOv3VG")
        )
        assert team.publication_status == "absent"
        assert any(d.code == code for d in result.diagnostics)


def test_capture_follows_only_public_same_event_links(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    requests = []

    def respond(r):
        requests.append(r.url.path)
        if r.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /")
        if "/roster/" in r.url.path:
            return httpx.Response(
                200,
                text=(
                    '<a href="/teamlist/public/E/a">ok</a><a '
                    'href="https://evil.test/teamlist/public/E/b">bad</a><a '
                    'href="/teamlist/public/OTHER/c">bad</a><a '
                    'href="/teamlist/private/E/d">bad</a>'
                ),
            )
        return httpx.Response(200, text="<html>Public</html>")

    scraper = RK9HTMLScraper(
        client=httpx.Client(transport=httpx.MockTransport(respond)), crawl_delay=0
    )
    assert hasattr(scraper, "capture_tournament"), "Public capture adapter is missing"
    capture = scraper.capture_tournament("E")
    assert "team:a" in capture.documents
    assert not any("evil" in str(d.url) for d in capture.documents.values())
    assert "/teamlist/public/OTHER/c" not in requests and "/teamlist/private/E/d" not in requests


def test_robots_requires_explicit_operator_override():
    def respond(r):
        return httpx.Response(
            200,
            text="User-agent: *\nDisallow: /roster/\n"
            if r.url.path == "/robots.txt"
            else "<html/>",
        )

    scraper = RK9HTMLScraper(
        client=httpx.Client(transport=httpx.MockTransport(respond)), crawl_delay=0
    )
    with pytest.raises(PermissionError):
        scraper._fetch_html("roster/E")
    assert (
        "public_scraping_authorized" in __import__("inspect").signature(RK9HTMLScraper).parameters
    )
    authorized = RK9HTMLScraper(
        client=scraper.client, crawl_delay=0, public_scraping_authorized=True
    )
    assert authorized._fetch_html("roster/E") == "<html/>"


def test_robots_transport_failure_cannot_be_swallowed_by_override(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    requests = []

    def respond(request):
        requests.append(request.url.path)
        raise httpx.ReadTimeout("robots unavailable", request=request)

    scraper = RK9HTMLScraper(
        client=httpx.Client(transport=httpx.MockTransport(respond)),
        crawl_delay=0,
        public_scraping_authorized=True,
    )
    with pytest.raises(httpx.ReadTimeout):
        scraper._fetch_html("roster/event")
    assert requests == ["/robots.txt"] * 3


def test_team_document_origin_must_match_its_public_entry_link():
    c, _, _ = real_inputs("rk9")
    key = "team:003kOPkX8H90ISnOv3VG"
    doc = c.documents[key].model_copy(update={"url": "https://evil.test/sheet"})
    result = normalize(c.model_copy(update={"documents": c.documents | {key: doc}}))
    team = next(t for t in result.tournament.teams if t.entry_id.endswith("003kOPkX8H90ISnOv3VG"))
    assert team.publication_status == "absent"
    assert any(d.code == "team_origin_conflict" for d in result.diagnostics)
