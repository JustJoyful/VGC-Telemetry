"""Hash-pinned retained community catalogs and explicit official registration pools."""

import hashlib
import json
import re
from pathlib import Path

from app.v2.errors import unavailable
from core.catalog import load_catalog, to_id

PUBLIC_ROOT = Path(__file__).resolve().parent / "regulations/v2/public"


def _read(pointer):
    path = (PUBLIC_ROOT / pointer["path"]).resolve()
    if not path.is_relative_to(PUBLIC_ROOT.resolve()):
        raise ValueError("Reference path leaves retained directory")
    body = path.read_bytes()
    if hashlib.sha256(body).hexdigest() != pointer["sha256"]:
        raise ValueError("Retained reference hash mismatch")
    return path, body


def load_public_reference(manifest_hash: str | None) -> dict:
    try:
        digest = (
            manifest_hash or json.loads((PUBLIC_ROOT / "bundled.json").read_text())["manifest_hash"]
        )
        if not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError("Unsupported reference manifest identity")
        _, body = _read({"path": digest + ".json", "sha256": digest})
        manifest = json.loads(body)
        if manifest["schema"] != "public-reference-v1":
            raise ValueError("Unsupported reference schema")
        index_path, index_body = _read(manifest["index"])
        index = json.loads(index_body)
        if set(manifest["eligibility"]) != set(index["snapshots"]):
            raise ValueError("Incomplete eligibility sidecars")
        _read(manifest["license"])
        catalogs, eligibility = {}, {}
        for revision, entry in index["snapshots"].items():
            _read(entry)
            catalog = load_catalog(revision, index_path=index_path)
            _, sidecar_body = _read(manifest["eligibility"][revision])
            sidecar = json.loads(sidecar_body)
            _, pool = _read(sidecar["pool"])
            source = json.loads(re.search(r"const pokemons = (\[.*?\]);", pool.decode()).group(1))
            resolved = sidecar["resolved_rows"]
            ids = sidecar["eligible_registration_ids"]
            if (
                sidecar["catalog_revision"] != revision
                or sidecar["catalog_hash"] != entry["sha256"]
                or sidecar["source_row_count"] != len(source)
                or len(resolved) != len(source)
                or sidecar["evidence"]["content_hash"] != sidecar["pool"]["sha256"]
                or set(ids) != {row["canonical_id"] for row in resolved}
                or len(ids) != len(set(ids))
                or set(ids) - catalog.species.keys()
            ):
                raise ValueError("Invalid official eligibility sidecar")
            for i, row in enumerate(resolved):
                key = to_id(source[i][2])
                if (
                    row["source_index"] != i
                    or row["official_name"] != source[i][2]
                    or row["canonical_id"] != catalog.aliases.get(key, key)
                    or catalog.species[row["canonical_id"]].battle_only
                ):
                    raise ValueError("Official name mapping changed")
            catalogs[revision], eligibility[revision] = catalog, sidecar
        return {
            "manifest_hash": digest,
            "index_path": index_path,
            "index_hash": manifest["index"]["sha256"],
            "index": index,
            "catalogs": catalogs,
            "eligibility": eligibility,
        }
    except (OSError, KeyError, TypeError, ValueError, AttributeError) as error:
        raise unavailable("Retained reference is unavailable") from error
