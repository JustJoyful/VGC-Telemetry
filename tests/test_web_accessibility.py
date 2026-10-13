# ruff: noqa: F811
from pathlib import Path

from tests.v2_helpers import v2_client  # noqa: F401
from tests.web_helpers import Document


def test_pages_have_semantic_landmarks_and_labeled_controls(v2_client):
    client, _, _, _ = v2_client
    team_id = client.get("/v2/teams?limit=1").json()["items"][0]["team_id"]
    for path in [
        "/explorer",
        "/explorer/pokemon/floetteeternal",
        "/explorer/teams/" + team_id,
        "/explorer/api",
        "/explorer/about",
        "/explorer/teams",
    ]:
        page = client.get(path)
        assert page.status_code == 200
        tags = Document(page.text).tags
        for tag in ["header", "nav", "main", "footer", "h1"]:
            assert any(t == tag for t, _ in tags)
        assert any(t == "a" and a.get("href") == "#main" for t, a in tags)
        assert any(t == "a" and a.get("aria-current") == "page" for t, a in tags)
        labels = {a.get("for") for t, a in tags if t == "label"}
        for t, a in tags:
            if t in ["input", "select"] and a.get("type") != "hidden":
                assert a.get("id") in labels


def test_entity_art_has_alt_text_and_decorations_are_hidden(v2_client):
    client, _, _, _ = v2_client
    page = client.get("/explorer/pokemon/floetteeternal")
    tags = Document(page.text).tags
    images = [a for t, a in tags if t == "img"]
    assert images and all(a.get("alt") == "" for a in images)
    assert sum(a.get("class") == "float-sprite" for a in images) == 1
    assert any("sky" == a.get("class", "") and a.get("aria-hidden") == "true" for _, a in tags)
    assert all(a.get("aria-hidden") == "true" for t, a in tags if t == "svg")


def test_status_is_textual_and_focusable_controls_exist(v2_client):
    client, _, _, _ = v2_client
    page = client.get("/explorer")
    tags = Document(page.text).tags
    assert "Collection disabled" not in page.text and "How to read this" in page.text
    assert "Fresh" in page.text.split('class="usage-carousel"')[0]
    assert any(a.get("role") == "status" and a.get("aria-live") == "polite" for _, a in tags)
    assert any("data-theme-toggle" in a for t, a in tags if t == "button")
    assert any(
        "data-motion-toggle" in a and a.get("aria-pressed") == "false"
        for t, a in tags
        if t == "button"
    )
    assert any(t == "form" and a.get("method") == "get" for t, a in tags)
    assert client.get("/explorer?species_id=incineroar&limit=3").status_code == 200
    boosted = client.get("/explorer", headers={"HX-Request": "true", "HX-Boosted": "true"})
    assert "<main" in boosted.text and boosted.headers["HX-Reselect"] == "#main"


def test_reduced_motion_disables_nonessential_animation():
    root = Path("app/web/static/src")
    css = (root / "app.css").read_text()
    js = (root / "app.js").read_text()
    assert "@media(prefers-reduced-motion:reduce)" in css
    assert "animation:none!important" in css
    assert ":root[data-motion=paused]" in css
    assert "IntersectionObserver" not in js and "dataset.motion" in js
    assert "prefers-reduced-motion: reduce" in js
    assert "Query results updated" in js
    assert "[data-theme=light]" in css and "min-height:44px" in css


def test_static_assets_do_not_consume_api_rate_budget_and_web_429_is_html(v2_client, monkeypatch):
    from app.middleware.rate_limit import DynamoDBRateLimitMiddleware

    client, _, _, _ = v2_client
    monkeypatch.setattr(DynamoDBRateLimitMiddleware, "check_rate_limit", lambda *_: (False, 0, 17))
    assert client.get("/explorer/static/app.css").status_code == 200
    page = client.get("/explorer")
    assert page.status_code == 429 and page.headers["content-type"].startswith("text/html")
    assert page.headers["Retry-After"] == "17" and "Try again" in page.text
    assert client.get("/v2/teams").json()["error"]["code"] == "rate_limited"


def test_filter_placeholder_uses_readable_theme_color():
    css = Path("app/web/static/src/app.css").read_text()
    assert "input::placeholder{color:var(--muted);opacity:1}" in css
