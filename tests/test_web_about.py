# ruff: noqa: F811
from tests.v2_helpers import v2_client  # noqa: F401
from tests.web_helpers import Document


def test_about_explains_data_limits_and_links_methodology(v2_client):
    client, _, _, _ = v2_client
    page = client.get("/explorer/about")
    assert page.status_code == 200
    for text in [
        "Limitless",
        "Collection disabled",
        "cut evidence",
        "Unknown",
        "Stat Points",
        "legacy EV",
        "Registered Mega",
        "activation",
        "community",
        "24 hours",
        "Pokémon Company",
    ]:
        assert text in page.text
    links = [a["href"] for a, _ in Document(page.text).links if "/static/notes/" in a["href"]]
    assert len(links) >= 3
    for link in links:
        assert client.get(link).status_code == 200
    assert client.get("/explorer/about?credentials=secret").status_code == 422


def test_evidence_sections_render_real_body_content_and_tables(v2_client):
    client, _, _, _ = v2_client
    response = client.get("/explorer/about")
    assert response.status_code == 200
    tags = Document(response.text).tags
    topics = [a for t, a in tags if t == "details" and a.get("class") == "dex-card evidence-topic"]
    assert len(topics) == 6
    assert {a["id"] for a in topics if "open" in a} == {"sources", "unknowns"}
    for id in ["sources", "samples", "unknowns", "freshness", "reference", "artwork"]:
        body = response.text.split('id="' + id + '"')[1].split("</details>")[0]
        assert "<p>" in body and len(body) > 200
    assert "Rosters sampled" in response.text and "Tournaments" in response.text
    assert "2</strong>" in response.text
    assert "<table" in response.text and "Technical details" in response.text
    assert "Pixelify Sans" in response.text and "Atkinson Hyperlegible Next" in response.text

    from db.schema_public import public_state_key

    table = v2_client[2]
    table.delete_item(Key=public_state_key())
    unavailable = client.get("/explorer/about")
    assert unavailable.status_code == 200
    assert "Unavailable" in unavailable.text and "Unknown</strong>" in unavailable.text
    assert "<table" not in unavailable.text
