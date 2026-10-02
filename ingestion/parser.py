from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from core.models.contracts import TournamentExport
from db.schema import (
    match_sk,
    team_gsi1,
    team_sk,
    tournament_pk,
)


@dataclass
class ParsedTournament:
    export: TournamentExport
    species_counts: Counter[str]
    moves_counts: dict[str, Counter[str]]
    ability_counts: dict[str, Counter[str]]
    evspread_counts: dict[str, Counter[str]]
    partner_counts: Counter[tuple[str, str]]
    team_rows: list[dict[str, Any]]
    match_rows: list[dict[str, Any]]


def parse_tournament_export(payload: dict[str, Any]) -> ParsedTournament:
    """Validate tournament payload against Pydantic contract and aggregate metrics in memory.

    Reconstructs species, moveset, ability, EV spread, and bidirectional partner core totals
    using collections.Counter, ensuring deterministic and idempotent aggregation.
    """
    export = TournamentExport.model_validate(payload)

    species_counts: Counter[str] = Counter()
    moves_counts: dict[str, Counter[str]] = defaultdict(Counter)
    ability_counts: dict[str, Counter[str]] = defaultdict(Counter)
    evspread_counts: dict[str, Counter[str]] = defaultdict(Counter)
    partner_counts: Counter[tuple[str, str]] = Counter()

    team_rows: list[dict[str, Any]] = []
    tid = export.metadata.tid
    t_date = export.metadata.date

    for team in export.teams:
        team_species: list[str] = []
        for p in team.pokemon:
            sp = p.species
            team_species.append(sp)
            species_counts[sp] += 1
            for m in p.moves:
                moves_counts[sp][m] += 1
            ability_counts[sp][p.ability] += 1
            canonical_ev = p.evs.canonical()
            evspread_counts[sp][canonical_ev] += 1

        # Generate bidirectional partner co-occurrences (all 15 pairs * 2 = 30 directed pairs)
        n = len(team_species)
        for i in range(n):
            for j in range(i + 1, n):
                sp_a = team_species[i]
                sp_b = team_species[j]
                partner_counts[(sp_a, sp_b)] += 1
                partner_counts[(sp_b, sp_a)] += 1

        gsi1_pk, gsi1_sk = team_gsi1(team.player_id, t_date, tid)
        team_row = {
            "PK": tournament_pk(tid),
            "SK": team_sk(team.player_id),
            "GSI1PK": gsi1_pk,
            "GSI1SK": gsi1_sk,
            "player_id": team.player_id,
            "player_name": team.player_name,
            "standing": team.standing,
            "record": team.record,
            "pokemon": [p.model_dump(by_alias=True) for p in team.pokemon],
        }
        team_rows.append(team_row)

    match_rows: list[dict[str, Any]] = []
    for match in export.matches:
        match_row = {
            "PK": tournament_pk(tid),
            "SK": match_sk(match.round, match.match_id),
            "round": match.round,
            "match_id": match.match_id,
            "player1_id": match.player1_id,
            "player2_id": match.player2_id,
            "winner_id": match.winner_id,
        }
        if match.score:
            match_row["score"] = match.score
        match_rows.append(match_row)

    return ParsedTournament(
        export=export,
        species_counts=species_counts,
        moves_counts=dict(moves_counts),
        ability_counts=dict(ability_counts),
        evspread_counts=dict(evspread_counts),
        partner_counts=partner_counts,
        team_rows=team_rows,
        match_rows=match_rows,
    )
