import hashlib
import json
import shutil

import pytest
from fastapi.testclient import TestClient


def test_retained_reference_manifests_resolve_every_official_pool_row():
    from core.public_reference import load_public_reference

    reference = load_public_reference(None)
    assert (
        reference["index_hash"] == hashlib.sha256(reference["index_path"].read_bytes()).hexdigest()
    )
    for revision, expected in [("champions-mc-3661ce4", 262), ("champions-mb-3661ce4", 235)]:
        entry = reference["eligibility"][revision]
        assert entry["source_row_count"] == expected
        assert len(entry["resolved_rows"]) == expected
        assert len(entry["eligible_registration_ids"]) == (261 if expected == 262 else 235)
        assert set(entry["eligible_registration_ids"]) == {
            row["canonical_id"] for row in entry["resolved_rows"]
        }
        assert entry["evidence"]["path"] and entry["evidence"]["content_hash"]
        assert "floetteeternal" in entry["eligible_registration_ids"]
        assert "floettemega" not in entry["eligible_registration_ids"]


@pytest.fixture
def reference_client(monkeypatch):
    from app.main import app
    from app.middleware.rate_limit import DynamoDBRateLimitMiddleware

    monkeypatch.setenv("V2_CURSOR_SECRET", "reference-test-secret-only" * 2)
    monkeypatch.setattr(DynamoDBRateLimitMiddleware, "check_rate_limit", lambda *_: (True, 60, 60))
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def test_reference_routes_legal_pool_aliases_and_learnset_pages(reference_client):
    from core.catalog import load_catalog
    from core.public_reference import load_public_reference

    client = reference_client
    regs = client.get("/v2/regulations").json()
    assert {r["rules_id"] for r in regs["items"]} == {"M-B", "M-C"}
    assert all(r["effective_from"] and r["effective_until"] for r in regs["items"])
    assert all("M-A" in r["coverage"] and "patch" in r["coverage"] for r in regs["items"])
    floette = client.get("/v2/pokemon/Floette?catalog_revision=champions-mc-3661ce4").json()
    assert floette["pokemon_id"] == "floetteeternal"  # official-name alias
    assert floette["species_id"] == "floette"
    assert floette["registration_eligibility"] == "confirmed"
    assert client.get("/v2/pokemon/floetteeternal").json()["pokemon_id"] == "floetteeternal"
    mega = client.get("/v2/pokemon/floettemega").json()
    assert mega["registration_eligibility"] == "unknown" and mega["is_mega"]
    assert "community" in mega["authority"]
    assert "moves" not in mega and mega["learnset_count"] > 0
    assert mega["types"] and mega["base_stats"]
    # M-C source rows 240/241 both explicitly map Squawkabilly to one canonical ID.
    for revision, expected in [("champions-mc-3661ce4", 261), ("champions-mb-3661ce4", 235)]:
        params = {"catalog_revision": revision, "legal_only": True, "limit": 100}
        identities = []
        while True:
            response = client.get("/v2/pokemon", params=params)
            assert response.status_code == 200, response.text
            data = response.json()
            identities.extend(p["pokemon_id"] for p in data["items"])
            if not data["next_cursor"]:
                break
            params["cursor"] = data["next_cursor"]
        assert len(identities) == len(set(identities)) == expected
    params = {"limit": 2}
    moves, token, details = [], None, {}
    while True:
        response = client.get("/v2/pokemon/floettemega/learnset", params=params)
        assert response.status_code == 200, response.text
        page = response.json()
        token = token or page["snapshot"]
        assert page["snapshot"] == token
        moves.extend(m["move_id"] for m in page["items"])
        details.update({m["move_id"]: m for m in page["items"]})
        if not page["next_cursor"]:
            break
        params["cursor"] = page["next_cursor"]
    ref = load_public_reference(None)
    catalog = load_catalog("champions-mc-3661ce4", index_path=ref["index_path"])
    assert moves == sorted(catalog.species["floettemega"].move_ids)
    for move, value in details.items():
        assert value["details"] == catalog.moves[move].details
        assert (
            value["catalog_hash"]
            == "33c614be1bc47c123ad112facf34da29bc9b9918852a4a432bb9b131e37b6862"
        )
    assert client.get("/v2/pokemon", params={"catalog_revision": "missing"}).status_code == 404


def test_reference_configuration_failure_does_not_break_health(reference_client, monkeypatch):
    monkeypatch.delenv("V2_CURSOR_SECRET")
    assert reference_client.get("/v2/pokemon").status_code == 503
    assert reference_client.get("/v2/regulations").status_code == 503
    assert reference_client.get("/health").status_code == 200


def test_detail_and_learnset_reject_collection_only_filters(reference_client):
    for route in ("/v2/pokemon/floettemega", "/v2/pokemon/floettemega/learnset"):
        assert reference_client.get(route, params={"legal_only": True}).status_code == 422


def test_old_reference_cursor_survives_mutable_index_and_default_change(
    reference_client, monkeypatch, tmp_path
):
    import core.public_reference as references

    first = reference_client.get("/v2/pokemon", params={"limit": 1}).json()
    original = references.load_public_reference(None)
    shutil.copytree(references.PUBLIC_ROOT, tmp_path / "retained")
    monkeypatch.setattr(references, "PUBLIC_ROOT", tmp_path / "retained")
    (tmp_path / "mutable-index.json").write_text('{"snapshots": {}}')
    monkeypatch.setenv("V2_DEFAULT_CATALOG_REVISION", "champions-mb-3661ce4")
    last = reference_client.get("/v2/pokemon", params={"limit": 1, "cursor": first["next_cursor"]})
    assert last.status_code == 200, last.text
    assert last.json()["snapshot"] == first["snapshot"]
    assert last.json()["items"][0]["catalog_revision"] == "champions-mc-3661ce4"
    entry = json.loads(original["index_path"].read_text())["snapshots"]["champions-mc-3661ce4"]
    retained = references.load_public_reference(None)["index_path"].parent / entry["path"]
    retained.write_text("synthetic corrupt catalog")
    assert (
        reference_client.get("/v2/pokemon", params={"snapshot": first["snapshot"]}).status_code
        == 503
    )


def test_incomplete_eligibility_sidecar_is_rejected(tmp_path, monkeypatch):
    import core.public_reference as references
    from app.v2.errors import V2Error

    shutil.copytree(references.PUBLIC_ROOT, tmp_path / "retained")
    monkeypatch.setattr(references, "PUBLIC_ROOT", tmp_path / "retained")
    ref = references.load_public_reference(None)
    manifest_path = references.PUBLIC_ROOT / (ref["manifest_hash"] + ".json")
    manifest = json.loads(manifest_path.read_text())
    pointer = manifest["eligibility"]["champions-mc-3661ce4"]
    sidecar = json.loads((references.PUBLIC_ROOT / pointer["path"]).read_text())
    sidecar["resolved_rows"] = sidecar["resolved_rows"][:-1]
    body = json.dumps(sidecar).encode()
    (references.PUBLIC_ROOT / "synthetic-incomplete.json").write_bytes(body)
    pointer.update(path="synthetic-incomplete.json", sha256=hashlib.sha256(body).hexdigest())
    body = json.dumps(manifest).encode()
    digest = hashlib.sha256(body).hexdigest()
    (references.PUBLIC_ROOT / (digest + ".json")).write_bytes(body)
    with pytest.raises(V2Error) as failure:
        references.load_public_reference(digest)
    assert failure.value.status == 503


def test_exact_name_choices_moves_and_revision_visibility(reference_client):
    client = reference_client
    for name in ["floetteeternal", "Floette", "Floette Eternal"]:
        response = client.get("/v2/pokemon", params={"name": name})
        assert response.status_code == 200, response.text
        assert [r["pokemon_id"] for r in response.json()["items"]] == ["floetteeternal"]
        assert response.json()["lookup_status"] == "exact"
    forms = client.get("/v2/pokemon", params={"name": "Rotom", "limit": 1})
    assert forms.status_code == 200, forms.text
    assert forms.json()["lookup_status"] == "ambiguous" and forms.json()["next_cursor"]
    assert client.get("/v2/pokemon", params={"name": "missing"}).status_code == 404
    moves = client.get(
        "/v2/moves", params={"name": " PRO-TECT ", "catalog_revision": "champions-mb-3661ce4"}
    )
    assert moves.status_code == 200, moves.text
    assert [m["move_id"] for m in moves.json()["items"]] == ["protect"]
    assert moves.json()["items"][0]["catalog_revision"] == "champions-mb-3661ce4"
    assert client.get("/v2/moves/missing").status_code == 404
    assert client.get("/v2/moves/Protect").json()["move_id"] == "protect"
    assert client.get("/v2/moves", params={"catalog_revision": "missing"}).status_code == 404
    first = client.get("/v2/moves", params={"limit": 1}).json()
    last = client.get("/v2/moves", params={"limit": 1, "cursor": first["next_cursor"]})
    assert last.status_code == 200 and last.json()["snapshot"] == first["snapshot"]
    assert (
        client.get(
            "/v2/moves", params={"limit": 1, "cursor": first["next_cursor"], "name": "Protect"}
        ).status_code
        == 400
    )


def test_name_lookup_counts_ambiguity_metadata_in_response_bound():
    from app.models.v2_queries import PokemonQuery
    from app.routers.v2.reference import reference_page
    from app.v2.config import V2ReadConfig
    from app.v2.errors import V2Error
    from core.public_reference import load_public_reference
    from ingestion.staging import canonical_bytes

    reference = load_public_reference(None)
    query = PokemonQuery(name="Floette", species_id="rotom")
    config = V2ReadConfig(secret=b"secret" * 8, clock=lambda: 100)
    args = dict(
        catalog_revision=config.default_catalog_revision,
        reference_manifest_hash=reference["manifest_hash"],
        query=query,
        position=None,
        limit=25,
    )
    page = reference_page(**args, config=config)
    size = len(canonical_bytes(page.model_dump(mode="json")))
    assert page.items == [] and page.lookup_status == "exact"
    with pytest.raises(V2Error) as failure:
        reference_page(
            **args,
            config=V2ReadConfig(secret=config.secret, clock=config.clock, response_bytes=size - 1),
        )
    assert failure.value.status == 503
