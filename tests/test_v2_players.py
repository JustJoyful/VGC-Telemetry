# ruff: noqa: F811
from tests.v2_helpers import v2_client  # noqa: F401


def test_source_scoped_player_history_keeps_context(v2_client):
    client, _, _, _ = v2_client
    response = client.get("/v2/players/limitless:axtheb", params={"limit": 1})
    assert response.status_code == 200, response.text
    assert response.json()["items"][0]["player_id"] == "limitless:axtheb"
    assert client.get("/v2/players/limitless:missing").status_code == 404
    assert client.get("/v2/players/display-name").status_code == 422
