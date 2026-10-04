"""Capture integrity tests: altered bytes or mislabeled evidence must be detectable."""

import hashlib
import importlib.util
import json
from pathlib import Path

import httpx
import pytest


def recorder():
    path = Path("scripts/capture_sources.py")
    assert path.exists(), "Public source recorder is not implemented"
    spec = importlib.util.spec_from_file_location("capture_sources", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_capture_retains_exact_body_and_metadata(tmp_path):
    body = '{"name":"Pokémon", "player":"public-id"}\n'.encode()
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, content=body, headers={"Content-Type": "application/json"}
            )
        )
    )
    path = recorder().capture_public_url(
        "https://play.limitlesstcg.com/api/games", output_dir=tmp_path, client=client
    )
    metadata = json.loads(path.read_text())
    assert (tmp_path / metadata["body_path"]).read_bytes() == body
    assert metadata["sha256"] == hashlib.sha256(body).hexdigest()
    assert metadata["url"] == "https://play.limitlesstcg.com/api/games"
    assert metadata["status"] == 200
    assert metadata["captured_at"].endswith("+00:00")


def test_http_failure_is_evidence_not_a_successful_capture(tmp_path):
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(403, text="Forbidden"))
    )
    path = recorder().capture_public_url(
        "https://rk9.gg/events", output_dir=tmp_path, client=client
    )
    assert json.loads(path.read_text())["status"] == 403


def test_capture_refuses_credentials_and_nonpublic_urls(tmp_path):
    for url in ["http://127.0.0.1/", "https://user:secret@rk9.gg/events", "file:///etc/passwd"]:
        with pytest.raises(ValueError):
            recorder().capture_public_url(url, output_dir=tmp_path, client=httpx.Client())


def test_fixture_manifest_hashes_and_origins():
    path = Path("tests/fixtures/sources/manifest.json")
    assert path.exists(), "No captured source evidence manifest"
    manifest = json.loads(path.read_text())
    assert manifest["captures"], "An empty manifest cannot establish source evidence"
    for entry in manifest["captures"]:
        metadata = json.loads((path.parent / entry["metadata_path"]).read_text())
        body = (path.parent / entry["metadata_path"]).parent / metadata["body_path"]
        assert hashlib.sha256(body.read_bytes()).hexdigest() == metadata["sha256"]
        assert metadata["captured_at"] and metadata["url"].startswith("https://")
        assert entry["origin"] in {"live_public", "documentation", "synthetic"}
        if entry.get("admission_evidence"):
            assert entry["origin"] == "live_public" and metadata["status"] == 200
