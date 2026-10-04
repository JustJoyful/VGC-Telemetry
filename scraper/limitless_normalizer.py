"""Pure normalization of recorded Limitless responses; network access stays in the client."""

import hashlib
import json

from pydantic import ValidationError

from core.catalog import validate_known_fields
from core.models.catalog import CatalogSnapshot
from core.models.champions import (
    ChampionsTournament,
    Diagnostic,
    MatchResultV2,
    NormalizationResult,
    SourceCapture,
    TeamRosterV2,
)
from core.source_admission import SourceAdmission, classify_event, document_evidence
from scraper.pokemon_fields import pokemon_from_fields


def normalize_limitless_capture(
    capture: SourceCapture, admission: SourceAdmission, catalog: CatalogSnapshot
) -> NormalizationResult:
    identified = classify_event(capture, admission)
    if identified.status != "verified":
        return NormalizationResult(
            admission_status=identified.status, diagnostics=identified.diagnostics
        )
    diagnostics = []
    teams = []
    matches = []

    def diagnose(code, path, message, key):
        diagnostics.append(
            Diagnostic(
                code=code, path=path, message=message, evidence=[document_evidence(capture, key)]
            )
        )

    for key in ["standings", "pairings"]:
        if key not in capture.documents or capture.documents[key].status != 200:
            diagnostics.append(
                Diagnostic(
                    code="source_unavailable", path=key, message="Required document is unavailable"
                )
            )
            return NormalizationResult(admission_status="pending", diagnostics=diagnostics)
    standings = json.loads(capture.documents["standings"].body)
    pairings = json.loads(capture.documents["pairings"].body)
    if not isinstance(standings, list) or not isinstance(pairings, list):
        raise ValueError("Standings and pairings must be arrays")
    seen = set()
    for i, row in enumerate(standings):
        path = f"teams.{i}"
        ev = document_evidence(capture, "standings")
        try:
            if not isinstance(row, dict):
                raise ValueError("Entrant must be an object")
            player = row.get("player")
            if not isinstance(player, str) or not player.strip():
                raise ValueError("Missing stable scalar player ID")
            if player in seen:
                raise ValueError("Duplicate entry identity")
            seen.add(player)
            raw = row.get("decklist")
            if raw is not None and not isinstance(raw, list):
                raise ValueError("Unexpected team-list shape")
            pokemon = []
            for slot, fields in enumerate(raw or []):
                try:
                    if not isinstance(fields, dict):
                        raise ValueError("Pokemon slot must be an object")
                    pokemon.append(
                        pokemon_from_fields(
                            fields, catalog, ev, f"{path}.pokemon.{slot}", diagnostics
                        )
                    )
                except (ValidationError, ValueError, TypeError) as error:
                    diagnose("invalid_pokemon", f"{path}.pokemon.{slot}", str(error), "standings")
            teams.append(
                TeamRosterV2(
                    entry_id=f"limitless:{capture.source_event_id}:{player}",
                    player_id=f"limitless:{player}",
                    player_name=row.get("name"),
                    standing=row.get("placing"),
                    record=row.get("record"),
                    pokemon=pokemon,
                    publication_status="complete"
                    if len(pokemon) == 6
                    else ("partial" if raw else "absent"),
                    field_evidence={"entry_id": [ev], "player_id": [ev]},
                )
            )
        except (ValidationError, ValueError, TypeError) as error:
            diagnose("invalid_entry", path, str(error), "standings")
    seen = set()
    for i, row in enumerate(pairings):
        try:
            if not isinstance(row, dict):
                raise ValueError("Pairing must be an object")

            def player(value):
                if value in (None, ""):
                    return None
                if not isinstance(value, str):
                    raise ValueError("Participant must be a scalar ID")
                return "limitless:" + value

            p1, p2 = player(row.get("player1")), player(row.get("player2"))
            winner = row.get("winner")
            wid = player(winner) if isinstance(winner, str) and winner else None
            if winner is None:
                outcome = "pending"
            elif p1 and p2:
                outcome = (
                    "tie"
                    if type(winner) is int and winner == 0
                    else (
                        "double_loss"
                        if type(winner) is int and winner == -1
                        else ("win" if wid else "unknown")
                    )
                )
            elif p1 and not p2:
                outcome = (
                    "bye"
                    if wid == p1
                    else ("automatic_loss" if type(winner) is int and winner == -1 else "unknown")
                )
            else:
                outcome = "unknown"
            identity = {k: row.get(k) for k in ["phase", "round", "table", "match"]}
            if row.get("table") is None and not row.get("match"):
                identity.update(player1=row.get("player1"), player2=row.get("player2"))
            digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]
            mid = f"limitless:{capture.source_event_id}:{digest}"
            if mid in seen:
                raise ValueError("Duplicate match identity")
            seen.add(mid)
            matches.append(
                MatchResultV2(
                    match_id=mid,
                    phase=row.get("phase"),
                    round=row.get("round"),
                    table=row.get("table"),
                    bracket_label=row.get("match"),
                    player1_id=p1,
                    player2_id=p2,
                    winner_id=wid,
                    outcome=outcome,
                    score=row.get("score"),
                    evidence=[document_evidence(capture, "pairings")],
                )
            )
        except (ValidationError, ValueError, TypeError) as error:
            diagnose("invalid_match", f"matches.{i}", str(error), "pairings")
    result = validate_known_fields(
        ChampionsTournament(metadata=identified.metadata, teams=teams, matches=matches), catalog
    )
    result.diagnostics = diagnostics + result.diagnostics
    return result
