# ruff: noqa: F811
import re
from pathlib import Path
from urllib.parse import urlsplit

from tests.v2_helpers import v2_client  # noqa: F401


def test_local_assets_are_served_with_the_web_document(v2_client):
    client, _, _, _ = v2_client
    page = client.get("/explorer")
    assets = re.findall(r'(?:src|href)="(/explorer/static/[^\"]+)"', page.text)
    assert any(urlsplit(url).path.endswith("app.css") for url in assets)
    assert any(url.endswith("htmx.min.js") for url in assets)
    assert "<script>" + Path("app/web/static/app.js").read_text() + "</script>" in page.text
    assert not any(url.endswith("app.js") for url in assets)
    for url in assets:
        response = client.get(url)
        assert response.status_code == 200 and response.content
    css = client.get("/explorer/static/app.css").text
    font_urls = re.findall(r"url\([\"\']?([^\)\"\']+\.woff2)", css)
    assert font_urls
    for path in font_urls:
        assert client.get("/explorer/static/" + path).status_code == 200
    assert "https://cdn" not in page.text


def test_runtime_images_discard_frontend_build_sources():
    for file, root in [("Dockerfile", "/var/task"), ("Dockerfile.local", "/work")]:
        source = Path(file).read_text()
        assert f"RUN rm -r {root}/app/web/static/src" in source
        assert "COPY --from=web-assets /build/app/web/static/" in source
