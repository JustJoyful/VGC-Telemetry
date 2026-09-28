from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.db.queries import get_meta_item, query_meta_stat
from core.config import get_active_regulation
from db.schema import (
    ability_usage_sk,
    evspread_usage_sk,
    moves_usage_sk,
    species_usage_sk,
)

router = APIRouter(prefix="/v1/meta", tags=["Meta"])


@router.get("")
def get_meta_statistics(
    regulation: str | None = Query(
        None, description="Regulation ID (defaults to active regulation)"
    ),
    tournament_id: str | None = Query(
        None, description="Optional tournament ID for tournament-scoped stats"
    ),
    metric: str = Query(
        "usage", description="Metric type: usage, moves, ability, evspread, partner"
    ),
    species: str | None = Query(None, description="Species name for detailed breakdown"),
) -> dict[str, Any]:
    """Query pre-aggregated competitive Pokémon Champions meta statistics."""
    reg = regulation or get_active_regulation().get("regulation_id", "M-C")
    target_pk = f"TOURNAMENT#{tournament_id}" if tournament_id else reg
    metric_lower = metric.lower()

    if metric_lower == "usage":
        if species:
            item = get_meta_item(target_pk, species_usage_sk(species))
            if not item:
                raise HTTPException(
                    status_code=404,
                    detail=f"No usage stats found for species '{species}'",
                )
            return {"regulation": reg, "metric": "usage", "species": species, "data": item}
        items = query_meta_stat(target_pk, "USAGE#")
        return {"regulation": reg, "metric": "usage", "data": items}

    elif metric_lower == "moves":
        if not species:
            raise HTTPException(
                status_code=400,
                detail="Query parameter 'species' is required for moves metric",
            )
        item = get_meta_item(target_pk, moves_usage_sk(species))
        if not item:
            raise HTTPException(
                status_code=404,
                detail=f"No move stats found for species '{species}'",
            )
        return {"regulation": reg, "metric": "moves", "species": species, "data": item}

    elif metric_lower == "ability":
        if not species:
            raise HTTPException(
                status_code=400,
                detail="Query parameter 'species' is required for ability metric",
            )
        item = get_meta_item(target_pk, ability_usage_sk(species))
        if not item:
            raise HTTPException(
                status_code=404,
                detail=f"No ability stats found for species '{species}'",
            )
        return {"regulation": reg, "metric": "ability", "species": species, "data": item}

    elif metric_lower == "evspread":
        if not species:
            raise HTTPException(
                status_code=400,
                detail="Query parameter 'species' is required for evspread metric",
            )
        item = get_meta_item(target_pk, evspread_usage_sk(species))
        if not item:
            raise HTTPException(
                status_code=404,
                detail=f"No EV spread stats found for species '{species}'",
            )
        return {"regulation": reg, "metric": "evspread", "species": species, "data": item}

    elif metric_lower == "partner":
        if species:
            # Bidirectional zero-scan prefix query: PARTNER#<species>#
            items = query_meta_stat(target_pk, f"PARTNER#{species}#")
            return {"regulation": reg, "metric": "partner", "species": species, "data": items}
        items = query_meta_stat(target_pk, "PARTNER#")
        return {"regulation": reg, "metric": "partner", "data": items}

    else:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown metric '{metric}'. "
                "Supported metrics: usage, moves, ability, evspread, partner"
            ),
        )
