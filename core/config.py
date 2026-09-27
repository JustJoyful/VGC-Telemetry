import json
from pathlib import Path
from typing import Any

_REGULATIONS_DIR = Path(__file__).parent / "regulations"
_CONFIG_CACHE: dict[str, dict[str, Any]] = {}


def get_regulation_config(regulation_id: str = "M-C") -> dict[str, Any]:
    """Load and cache the cumulative regulation snapshot for the given regulation ID."""
    clean_id = regulation_id.replace("-", "_").lower()
    if clean_id in _CONFIG_CACHE:
        return _CONFIG_CACHE[clean_id]

    config_path = _REGULATIONS_DIR / f"{clean_id}.json"
    if not config_path.exists():
        raise ValueError(f"Unknown regulation snapshot '{regulation_id}' at {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        data = json.load(f)
        _CONFIG_CACHE[clean_id] = data
        return data


def is_legal_mega_stone(item: str | None, regulation_id: str = "M-C") -> bool:
    """Check if an item is an enumerated legal standard or Z Mega Stone.

    Uses exact closed-set membership — never substring matching.
    """
    if not item:
        return False
    config = get_regulation_config(regulation_id)
    std_stones = set(config.get("standard_mega_stones", []))
    z_stones = set(config.get("z_mega_stones", []))
    return item in (std_stones | z_stones)


def is_legal_z_mega_stone(item: str | None, regulation_id: str = "M-C") -> bool:
    """Check if an item is a legal Z Mega Stone for Z Mega Evolution."""
    if not item:
        return False
    config = get_regulation_config(regulation_id)
    return item in set(config.get("z_mega_stones", []))


def is_valid_form(form: str | None, regulation_id: str = "M-C") -> bool:
    """Validate that a form string belongs to the closed regulation form vocabulary."""
    if form is None:
        return True
    config = get_regulation_config(regulation_id)
    return form in set(config.get("form_vocabulary", []))
