"""Record public source evidence without rewriting source bodies.

Usage: python scripts/capture_sources.py URL --output-dir DIRECTORY
Only approved public source hosts are accepted; redirects are recorded, not followed.
"""

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx

PUBLIC_HOSTS = {
    "play.limitlesstcg.com",
    "docs.limitlesstcg.com",
    "web-view.app.pokemonchampions.jp",
    "champions-news.pokemon-home.com",
    "champions.pokemon.com",
    "www.pokemon.com",
    "resource.pokemon.com",
    "www.trainerchampionships.com",
    "championships.pokemon.com",
    "raw.githubusercontent.com",
    "api.github.com",
    "victoryroad.pro",
    "limitlessvgc.com",
}


def capture_public_url(url: str, *, output_dir: Path, client: httpx.Client) -> Path:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in PUBLIC_HOSTS
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
    ):
        raise ValueError("Only credential-free HTTPS URLs on approved public hosts are supported")
    response = client.get(url, follow_redirects=False, timeout=15)
    captured_at = datetime.now(timezone.utc).isoformat()
    digest = hashlib.sha256(response.content).hexdigest()
    identifier = hashlib.sha256((url + captured_at + digest).encode()).hexdigest()
    output_dir.mkdir(parents=True, exist_ok=True)
    body_path = output_dir / f"{identifier}.body"
    body_path.write_bytes(response.content)
    metadata = {
        "url": url,
        "captured_at": captured_at,
        "status": response.status_code,
        "content_type": response.headers.get("content-type", ""),
        "sha256": digest,
        "body_path": body_path.name,
    }
    metadata_path = output_dir / f"{identifier}.json"
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return metadata_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("urls", nargs="+")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    with httpx.Client(
        headers={"User-Agent": "VGCTelemetryBot/1.0 (public source verification)"}
    ) as client:
        for url in args.urls:
            path = capture_public_url(url, output_dir=args.output_dir, client=client)
            print(path)


if __name__ == "__main__":
    main()
