"""Rebuild Phase 0 bundles/catalogs from recorded evidence and resolved Dex exports.

Run the CJS exporter for M-C/M-B first into --resolved-dir/catalog-mc.json and
catalog-mb.json. No network requests or runtime reference dependencies are used here.
"""

import argparse
import hashlib
import json
import re
from pathlib import Path

from core.models.catalog import CatalogSnapshot
from core.models.champions import SourceCapture
from core.source_admission import SourceAdmission

REVISION = "3661ce40bf9001d185ce078b8920e12304204609"
ROOT = Path(__file__).resolve().parents[1]
CAPTURES = ROOT / "tests/fixtures/sources/captures"


OFFICIAL_NAMES = {
    "floette": "floetteeternal",
    "meowsticmale": "meowstic",
    "indeedeemale": "indeedee",
    "basculegionmale": "basculegion",
    "gourgeistmediumvariety": "gourgeist",
    "gourgeistjumbovariety": "gourgeistsuper",
    "lycanrocmiddayform": "lycanroc",
    "toxtricityampedform": "toxtricity",
    "rotomrotom": "rotom",
    "taurospaldeanformcombatbreed": "taurospaldeacombat",
    "taurospaldeanformblazebreed": "taurospaldeablaze",
    "taurospaldeanformaquabreed": "taurospaldeaaqua",
}


def official_id(name):
    key = re.sub("[^a-z0-9]", "", name.lower())
    result = OFFICIAL_NAMES.get(key, key)
    for suffix, canonical in [
        ("alolanform", "alola"),
        ("galarianform", "galar"),
        ("hisuianform", "hisui"),
        ("female", "f"),
        ("smallvariety", "small"),
        ("largevariety", "large"),
        ("midnightform", "midnight"),
        ("duskform", "dusk"),
        ("lowkeyform", "lowkey"),
    ]:
        result = result.replace(suffix, canonical)
    if result.startswith("rotom") and result.endswith("rotom") and result != "rotom":
        result = result[:-5]
    return key, result


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def recorded():
    docs = {}
    for path in sorted(CAPTURES.glob("*.json")):
        meta = json.loads(path.read_text())
        body = (path.parent / meta["body_path"]).read_bytes()
        if hashlib.sha256(body).hexdigest() != meta["sha256"]:
            raise ValueError(f"Altered capture {path}")
        docs[meta["url"]] = (meta, body.decode("utf-8"))
    return docs


def build(resolved_dir):
    docs = recorded()
    admission_urls = set()

    def evidence(url):
        meta, _ = docs[url]
        if meta["status"] != 200:
            raise ValueError(f"Failed evidence {url}")
        admission_urls.add(url)
        return dict(
            url=url,
            captured_at=meta["captured_at"],
            document_key=meta["body_path"],
            content_hash=meta["sha256"],
        )

    official = "https://champions-news.pokemon-home.com/en/page/"
    formats = (
        f"https://raw.githubusercontent.com/smogon/pokemon-showdown/{REVISION}/config/formats.ts"
    )
    refs = [
        evidence(formats),
        evidence(
            f"https://raw.githubusercontent.com/smogon/pokemon-showdown/{REVISION}/data/mods/champions/scripts.ts"
        ),
        evidence(
            f"https://raw.githubusercontent.com/smogon/pokemon-showdown/{REVISION}/sim/team-validator.ts"
        ),
        evidence(
            f"https://raw.githubusercontent.com/smogon/pokemon-showdown/{REVISION}/sim/dex-formats.ts"
        ),
    ]
    license_url = f"https://raw.githubusercontent.com/smogon/pokemon-showdown/{REVISION}/LICENSE"
    evidence(license_url)
    (ROOT / "core/regulations/v2/UPSTREAM_LICENSE.txt").write_text(docs[license_url][1])
    index = {"snapshots": {}}
    mappings = {}
    for regulation, page, start, end in [
        ("M-C", "816", "2026-09-09T02:00:00Z", "2026-12-02T02:00:00Z"),
        ("M-B", "776", "2026-06-17T02:00:00Z", "2026-09-09T02:00:00Z"),
    ]:
        rid = regulation.replace("-", "").lower()
        snapshot_id = f"champions-{rid}-{REVISION[:7]}"
        data = json.loads((resolved_dir / f"catalog-{rid}.json").read_text())
        notice = docs[official + page + ".html"][1]
        eligible_url = re.search(r'href="(https://web-view.app[^"]+/pokemon.html)"', notice).group(
            1
        )
        eligible_body = docs[eligible_url][1]
        rows = json.loads(re.search(r"const pokemons = (\[.*?\]);", eligible_body).group(1))
        aliases = dict(official_id(row[2]) for row in rows)
        missing = set(aliases.values()) - data["species"].keys()
        if missing:
            raise ValueError(f"Resolved catalog misses official pool entries: {missing}")
        snapshot = CatalogSnapshot(
            **data,
            aliases=aliases,
            snapshot_id=snapshot_id,
            rules_id=regulation,
            game_data_revision=f"showdown-{REVISION}",
            effective_from=start,
            effective_until=end,
            verification_status="verified",
            coverage_complete=True,
            source_kind="community",
            upstream_revision=REVISION,
            evidence=[evidence(official + page + ".html"), evidence(eligible_url), *refs],
            notes=[
                f"All {len(rows)} entries of the official eligible list resolve in this snapshot.",
                "Complete resolved community Dex for the named VGC format; includes "
                "legal cosmetic and battle-only forms.",
                "Supplementary mechanics/learnsets from MIT-licensed Pokémon Showdown; "
                "not an official game data export.",
                "Unknown historical patches and M-A remain unverified. No date-only "
                "event admission.",
            ],
        )
        path = ROOT / "core/regulations/v2" / f"{snapshot_id}.json"
        write(path, snapshot.model_dump(mode="json", by_alias=True))
        index["snapshots"][snapshot_id] = dict(
            path=path.name, sha256=hashlib.sha256(path.read_bytes()).hexdigest()
        )
        mappings[regulation] = dict(
            game="VGC",
            platforms=["SWITCH"],
            rules_id=regulation,
            catalog_revision=snapshot_id,
            game_data_revision=snapshot.game_data_revision,
            battle_format="doubles",
            evidence=[evidence(official + page + ".html"), evidence(formats)],
        )
    write(ROOT / "core/regulations/v2/index.json", index)
    games = evidence("https://play.limitlesstcg.com/api/games")
    limitless = SourceAdmission(
        source="limitless", verified=True, evidence=[games], format_mappings=mappings
    )
    event_mappings = {}
    for event, label, start, report in [
        (
            "FR002-fiunEHp9wx4mh4",
            "2027 Frankfurt Regional Championships",
            "2026-09-26T06:30:00Z",
            "frankfurt",
        ),
        (
            "BA002-JL3KVbvivVKNAc",
            "2027 Baltimore Regional Championships",
            "2026-09-19T12:30:00Z",
            "baltimore",
        ),
    ]:
        url = f"https://victoryroad.pro/2027-{report}/"
        body = docs[url][1]
        if "M-C" not in body or "Pokémon Champions" not in body:
            raise ValueError(f"Report lacks game/regulation evidence: {url}")
        event_mappings[event] = mappings["M-C"] | dict(
            name=label,
            starts_at=start,
            evidence=[
                evidence(url),
                evidence("https://rk9.gg/tournament/" + event),
                *mappings["M-C"]["evidence"],
            ],
        )
    rk9 = SourceAdmission(
        source="rk9",
        verified=True,
        evidence=[evidence("https://rk9.gg/events/pokemon")],
        event_mappings=event_mappings,
        event_ids=list(event_mappings),
        public_scraping_authorized=True,
        max_team_pages=2,
    )
    write(
        ROOT / "core/sources/admission.json",
        {"sources": [a.model_dump(mode="json") for a in [limitless, rk9]]},
    )
    for source, event in [
        ("limitless", "6ab9204c880ed327106dddf4"),
        ("rk9", "FR002-fiunEHp9wx4mh4"),
    ]:
        selected = {}
        for url, (meta, body) in docs.items():
            if source == "limitless" and url.startswith(
                f"https://play.limitlesstcg.com/api/tournaments/{event}/"
            ):
                key = url.rsplit("/", 1)[1]
            elif source == "rk9" and url in [
                f"https://rk9.gg/{p}/{event}" for p in ["tournament", "roster", "pairings"]
            ]:
                key = {"tournament": "event", "roster": "roster", "pairings": "pairings"}[
                    url.split("/")[3]
                ]
            elif source == "rk9" and url.startswith(f"https://rk9.gg/teamlist/public/{event}/"):
                key = "team:" + url.rsplit("/", 1)[1]
            elif source == "rk9" and url == "https://rk9.gg/robots.txt":
                key = "robots"
            else:
                continue
            selected[key] = dict(
                url=url,
                body=body,
                status=meta["status"],
                content_type=meta["content_type"],
                content_hash=meta["sha256"],
            )
        capture = SourceCapture(
            source=source,
            source_event_id=event,
            fetched_at=max(docs[d["url"]][0]["captured_at"] for d in selected.values()),
            documents=selected,
        )
        write(
            ROOT / f"tests/fixtures/sources/{source}/accepted-capture.json",
            capture.model_dump(mode="json"),
        )
    entries = []
    for p in sorted(CAPTURES.glob("*.json")):
        meta = json.loads(p.read_text())
        entries.append(
            dict(
                metadata_path=f"captures/{p.name}",
                origin="live_public",
                admission_evidence=meta["url"] in admission_urls,
            )
        )
    write(
        ROOT / "tests/fixtures/sources/manifest.json",
        dict(
            captures=entries,
            gaps=[
                "RK9 robots excludes roster/pairings/public sheets; explicit operator "
                "scraping authorization is required.",
                "Only two RK9 public sheets per sample were captured; remaining linked "
                "sheets are explicitly not captured.",
                "RK9 masked player IDs and name-only pairings cannot establish "
                "cross-event player identity.",
                "M-A and intermediate historical game-data patches are not verified.",
                "Public sheets do not publish native Stat Points; missing allocations "
                "stay unknown.",
            ],
        ),
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resolved-dir", type=Path, required=True)
    build(parser.parse_args().resolved_dir)
