"""Explicitly synthetic contracts for boundary tests, never source-admission evidence."""

import hashlib
import json

from core.models.champions import CapturedDocument, SourceCapture

TIME = "2026-10-04T00:00:00+00:00"
EVIDENCE = {
    "url": "https://example.test/synthetic",
    "captured_at": TIME,
    "document_key": "synthetic",
    "content_hash": "a" * 64,
}


def capture(details=None, standings=None, pairings=None):
    bodies = {
        "details": details
        or {
            "id": "event",
            "game": "VGC",
            "format": "M-C",
            "platform": "SWITCH",
            "name": "Synthetic",
            "date": TIME,
        },
        "standings": standings or [],
        "pairings": pairings or [],
    }
    return SourceCapture(
        source="limitless",
        source_event_id="event",
        fetched_at=TIME,
        documents={
            name: CapturedDocument(
                url=f"https://play.limitlesstcg.com/api/tournaments/event/{name}",
                content_type="application/json",
                body=json.dumps(body),
                content_hash=hashlib.sha256(json.dumps(body).encode()).hexdigest(),
            )
            for name, body in bodies.items()
        },
    )


def catalog_data():
    return {
        "snapshot_id": "test-mc",
        "rules_id": "M-C",
        "game_data_revision": "test-revision",
        "effective_from": "2026-09-09T00:00:00+00:00",
        "effective_until": None,
        "verification_status": "verified",
        "coverage_complete": True,
        "source_kind": "synthetic",
        "upstream_revision": "test-revision",
        "evidence": [EVIDENCE],
        "species": {
            "gengar": {
                "name": "Gengar",
                "base_species_id": "gengar",
                "ability_ids": ["cursedbody"],
                "move_ids": ["protect", "shadowball"],
                "types": ["Ghost", "Poison"],
                "base_stats": {"hp": 60, "atk": 65, "def": 60, "spa": 130, "spd": 75, "spe": 110},
            },
            "gengarmega": {
                "name": "Gengar-Mega",
                "base_species_id": "gengar",
                "ability_ids": ["shadowtag"],
                "move_ids": ["protect", "shadowball"],
                "is_mega": True,
            },
        },
        "items": {
            "gengarite": {
                "name": "Gengarite",
                "mega_species_id": "gengar",
                "mega_variant_id": "gengarmega",
            },
            "sitrusberry": {"name": "Sitrus Berry"},
        },
        "abilities": {"cursedbody": {"name": "Cursed Body"}, "shadowtag": {"name": "Shadow Tag"}},
        "moves": {"protect": {"name": "Protect"}, "shadowball": {"name": "Shadow Ball"}},
        "natures": {"timid": {"name": "Timid", "plus": "spe", "minus": "atk"}},
        "aliases": {},
    }


def admission_data():
    return {
        "source": "limitless",
        "enabled": False,
        "verified": True,
        "evidence": [EVIDENCE],
        "format_mappings": {
            "M-C": {
                "game": "VGC",
                "platforms": ["SWITCH"],
                "rules_id": "M-C",
                "catalog_revision": "test-mc",
                "game_data_revision": "test-revision",
                "battle_format": "doubles",
                "evidence": [EVIDENCE],
            }
        },
    }
