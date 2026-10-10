"""Exact identities and attributed conflicts; observed tournament data is never mutated."""

from core.models.builds import BuildLink


def roster_identity(pokemon):
    return sorted((p.species_id, p.form_id or "") for p in pokemon)


def field_conflicts(left, right):
    for name in ("ability_id", "nature_id", "stat_points"):
        a, b = getattr(left, name), getattr(right, name)
        if a is not None and b is not None and a != b:
            return True
    if left.item.state != "unknown" and right.item.state != "unknown" and left.item != right.item:
        return True
    if left.moves is not None and right.moves is not None:
        a, b = set(left.moves), set(right.moves)
        if (
            left.moves_complete
            and not b <= a
            or right.moves_complete
            and not a <= b
            or len(a | b) > 4
        ):
            return True
    return False


def pokemon_conflicts(left, right):
    right_slots = {(p.species_id, p.form_id): p for p in right}
    return any(field_conflicts(p, right_slots[(p.species_id, p.form_id)]) for p in left)


def resolve_build(
    record, tournaments, *, previous_builds=(), cross_reference=None, tournament_source="limitless"
):
    candidates = []
    for tournament in tournaments:
        for team in tournament.teams:
            if cross_reference:
                cross = cross_reference
                exact = (
                    cross.source == record.source
                    and cross.source_event_id == record.event_id
                    and cross.source_player_id == record.player_id
                    and cross.target_source == tournament_source
                    and f"{cross.target_source}:{cross.target_event_id}"
                    == tournament.metadata.event_id
                    and cross.target_entry_id == team.entry_id
                )
            else:
                exact = (
                    record.event_source == tournament_source
                    and f"{record.event_source}:{record.event_id}" == tournament.metadata.event_id
                    and (not record.player_id or record.player_id == team.player_id)
                    and (not record.entrant_id or record.entrant_id == team.entry_id)
                    and bool(record.player_id or record.entrant_id)
                )
            if exact:
                candidates.append((tournament, team))
    result = BuildLink(
        revision_id=record.revision_id,
        status="unlinked",
        diagnostics=["no_exact_identity"],
        cross_reference=cross_reference,
    )
    if not candidates:
        return result
    if len(candidates) != 1:
        return result.model_copy(
            update={"status": "pending_conflict", "diagnostics": ["ambiguous_identity"]}
        )
    tournament, team = candidates[0]
    result = result.model_copy(
        update={
            "event_source": tournament_source,
            "event_id": tournament.metadata.event_id,
            "entry_id": team.entry_id,
        }
    )
    diagnostics = []
    if roster_identity(record.pokemon) != roster_identity(team.pokemon):
        diagnostics.append("roster_mismatch")
    elif pokemon_conflicts(record.pokemon, team.pokemon):
        diagnostics.append("observed_field_conflict")
    for previous in previous_builds:
        if previous.revision_id == record.revision_id:
            continue
        correction = (
            record.supersedes == previous.revision_id
            and record.source == previous.source
            and record.build_id == previous.build_id
            and record.author == previous.author
            and (record.event_source, record.event_id, record.player_id, record.entrant_id)
            == (previous.event_source, previous.event_id, previous.player_id, previous.entrant_id)
        )
        if correction:
            continue
        if (
            not record.supersedes
            and (record.source, record.build_id) == (previous.source, previous.build_id)
            and (record.document.content_hash != previous.document.content_hash)
        ):
            diagnostics.append("correction_evidence_required")
        if record.supersedes == previous.revision_id:
            diagnostics.append("invalid_correction")
        if roster_identity(record.pokemon) != roster_identity(previous.pokemon):
            diagnostics.append("supplementary_roster_conflict")
        elif pokemon_conflicts(record.pokemon, previous.pokemon):
            diagnostics.append("supplementary_field_conflict")
    if record.supersedes and not any(
        old.revision_id == record.supersedes for old in previous_builds
    ):
        diagnostics.append("correction_target_missing")
    return result.model_copy(
        update={
            "status": "pending_conflict" if diagnostics else "linked",
            "diagnostics": sorted(set(diagnostics)),
        }
    )
