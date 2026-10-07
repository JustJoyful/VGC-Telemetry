"""Evidence-preserving public projection and per-entry integer contributions."""

import json
from datetime import UTC
from itertools import combinations

from app.models.v2 import (
    Coverage,
    EventSummary,
    MatchView,
    Performance,
    PokemonBuild,
    PublicCut,
    PublicEvidence,
    TeamView,
)
from core.models.catalog import CatalogSnapshot
from core.models.champions import ChampionsTournament, SourceCapture
from core.models.public_read import (
    DailyContribution,
    PopulationKey,
    PublicEventBundle,
    population_id,
)
from core.models.publication import RevisionInput
from core.models.top_cut import CutDecision
from core.top_cut import derive_cut, entry_eligibility
from ingestion.contributions import check_size


def public_evidence(ref) -> dict:
    return PublicEvidence(
        **ref.model_dump(include={"url", "content_hash", "captured_at", "path"})
    ).model_dump(mode="json")


def population_for(meta, team=None) -> PopulationKey:
    return PopulationKey(
        source="limitless",
        game_id=meta.game_id,
        battle_format=meta.battle_format,
        division_id=team.division_id if team and team.division_id is not None else meta.division_id,
        competition_id=meta.competition_id,
        rules_id=meta.rules_id,
        catalog_revision=meta.catalog_revision,
        game_data_revision=meta.game_data_revision,
        match_formats=meta.match_formats,
        custom_rules=meta.custom_rules,
    )


def canonical_cores(
    identities: list[tuple[str, str]], *, size: int
) -> list[tuple[tuple[str, str], ...]]:
    if size not in (2, 3):
        raise ValueError("Core size must be 2 or 3")
    return list(combinations(sorted(set(identities)), size))


def identities(team, group_by):
    return sorted(
        {
            (p.species_id, (p.form_id or "base") if group_by == "form" else "base")
            for p in team.pokemon
        }
    )


def identity_text(identity):
    return "/".join(identity)


def core_text(core):
    return "+".join(identity_text(i) for i in core)


def performance_percentage(wins: int, losses: int, ties: int) -> float | None:
    played = wins + losses + ties
    return round((wins + 0.5 * ties) * 100 / played, 6) if played else None


def match_associations(
    tournament: ChampionsTournament, cut: CutDecision, *, populations: dict[str, PopulationKey]
) -> list[DailyContribution]:
    if cut.status != "verified_complete":
        return []
    by_player = {}
    for team in tournament.teams:
        by_player.setdefault(team.player_id, []).append(team)
    rows = []
    day = (
        tournament.metadata.starts_at.astimezone(UTC).date()
        if tournament.metadata.starts_at
        else None
    )
    for match in tournament.matches:
        if match.phase != cut.phase_id:
            continue
        sides = [by_player.get(p, []) for p in (match.player1_id, match.player2_id)]
        linked = all(len(s) == 1 for s in sides) and all(
            p in cut.participant_ids for p in (match.player1_id, match.player2_id)
        )
        if linked:
            linked = population_id(populations[sides[0][0].entry_id]) == population_id(
                populations[sides[1][0].entry_id]
            )
        for side, player in enumerate((match.player1_id, match.player2_id)):
            if len(sides[side]) != 1 or player not in cut.participant_ids:
                continue
            team = sides[side][0]
            pop = population_id(populations[team.entry_id])
            played = linked and match.outcome in ("win", "tie", "double_loss")
            values = dict(
                wins=int(played and match.outcome == "win" and match.winner_id == player),
                losses=int(
                    played
                    and (
                        match.outcome == "double_loss"
                        or match.outcome == "win"
                        and match.winner_id != player
                    )
                ),
                ties=int(played and match.outcome == "tie"),
                double_losses=int(played and match.outcome == "double_loss"),
                byes=int(match.outcome in ("bye", "automatic_loss")),
                unlinked=int(not linked),
                excluded_count=int(not played),
            )
            base = dict(
                event_id=tournament.metadata.event_id,
                entry_id=team.entry_id,
                population_id=pop,
                day=day,
                placement=team.standing,
            )
            rows.append(
                DailyContribution(
                    **base, metric="performance:team", identity=team.entry_id, **values
                )
            )
            if (
                not linked
                or team.publication_status != "complete"
                or sides[1 - side][0].publication_status != "complete"
            ):
                continue
            opponent = sides[1 - side][0]
            for group in ("form", "species"):
                focal = identities(team, group)
                other = identities(opponent, group)
                observations = [("species", identity_text(i), i in other) for i in focal]
                for size in (2, 3):
                    other_cores = set(canonical_cores(other, size=size))
                    observations.extend(
                        (f"core{size}", core_text(c), c in other_cores)
                        for c in canonical_cores(focal, size=size)
                    )
                for metric, identity, mirror in observations:
                    counts = (
                        values
                        if not (played and mirror)
                        else {**values, "wins": 0, "losses": 0, "ties": 0, "double_losses": 0}
                    )
                    rows.append(
                        DailyContribution(
                            **base,
                            metric="performance:" + metric,
                            identity=identity,
                            group_by=group,
                            mirrors=int(played and mirror),
                            **counts,
                        )
                    )
    return rows


def derive_public_event(
    input: RevisionInput, capture: SourceCapture, *, catalog: CatalogSnapshot, policy_version: str
) -> PublicEventBundle:
    tournament = input.result.tournament
    if not tournament or input.catalog_revision != catalog.snapshot_id:
        raise ValueError("Public derivation requires exact admitted catalog/input")
    meta = tournament.metadata
    cut = derive_cut(capture, input.result, policy_version=policy_version)
    population_keys = {team.entry_id: population_for(meta, team) for team in tournament.teams}
    populations = {population_id(v): v for v in population_keys.values()}
    if not populations:
        p = population_for(meta)
        populations[population_id(p)] = p
    raw = json.loads(capture.documents["standings"].body)
    mapped = {t.player_id for t in tournament.teams}
    rejected = {d.path for d in input.result.diagnostics if d.code == "invalid_entry"}
    coverage = Coverage(
        advertised_entrants=meta.entrant_count,
        standings_entries=len(raw),
        proven_cut_participants=len(cut.participant_ids),
        mapped_participants=len(set(cut.participant_ids) & mapped),
        invalid_rosters=sum(
            1
            for index, r in enumerate(raw)
            if f"teams.{index}" in rejected
            or (isinstance(r, dict) and "limitless:" + str(r.get("player")) not in mapped)
        ),
    )
    rows, contributions = [], []
    associations = match_associations(tournament, cut, populations=population_keys)

    def row(section, ordinal, kind, data, **metadata):
        value = dict(section=section, ordinal=ordinal, kind=kind, data=data, **metadata)
        check_size(value)
        rows.append(value)

    day = meta.starts_at.astimezone(UTC).date() if meta.starts_at else None
    # Numeric descending UTC timestamp, unknown dates sort after all dated events.
    order = (
        f"0:{9999999999999999 - int(meta.starts_at.timestamp() * 1_000_000):016d}"
        if meta.starts_at
        else "1:unknown"
    )
    for team in tournament.teams:
        rates = {
            field: sum(
                getattr(row, field)
                for row in associations
                if row.metric == "performance:team" and row.entry_id == team.entry_id
            )
            for field in (
                "wins",
                "losses",
                "ties",
                "double_losses",
                "mirrors",
                "byes",
                "unlinked",
                "excluded_count",
            )
        }
        eligibility = entry_eligibility(cut, player_id=team.player_id)
        setattr(
            coverage,
            team.publication_status + "_rosters",
            getattr(coverage, team.publication_status + "_rosters") + 1,
        )
        if eligibility.status != "included":
            field = (
                "excluded_membership" if eligibility.status == "excluded" else "unknown_membership"
            )
            setattr(coverage, field, getattr(coverage, field) + 1)
        pop = population_id(population_keys[team.entry_id])
        contributions.append(
            DailyContribution(
                event_id=input.event_id,
                entry_id=team.entry_id,
                population_id=pop,
                day=day,
                placement=team.standing,
                metric="coverage:" + team.publication_status,
                identity=team.entry_id,
                count=1,
                denominator=int(
                    cut.status == "verified_complete"
                    and eligibility.status == "included"
                    and team.publication_status == "complete"
                ),
                known_count=int(eligibility.status == "included"),
                unknown_count=int(eligibility.status == "unknown"),
                excluded_count=int(eligibility.status == "excluded"),
            )
        )
        pokemon = []
        for slot, mon in enumerate(team.pokemon):
            data = mon.model_dump(mode="json", by_alias=True, exclude={"field_evidence"})
            data["field_evidence"] = {
                k: [public_evidence(e) for e in refs[:4]] for k, refs in mon.field_evidence.items()
            }
            data["evidence_counts"] = {k: len(refs) for k, refs in mon.field_evidence.items()}
            for field, refs in mon.field_evidence.items():
                for index, ref in enumerate(refs):
                    row(
                        "EVIDENCE#TEAM#" + team.entry_id,
                        f"pokemon.{slot}.{field}:{index:08d}",
                        "evidence",
                        public_evidence(ref),
                    )
            pokemon.append(PokemonBuild(**data))
        team_provenance = []
        provenance_count = 0
        for field, refs in team.field_evidence.items():
            for index, ref in enumerate(refs):
                evidence = public_evidence(ref)
                provenance_count += 1
                if len(team_provenance) < 4:
                    team_provenance.append(evidence)
                row(
                    "EVIDENCE#TEAM#" + team.entry_id,
                    f"team.{field}:{index:08d}",
                    "evidence",
                    evidence,
                )
        view = TeamView(
            team_id=team.entry_id,
            event_id=input.event_id,
            player_id=team.player_id,
            player_name=team.player_name,
            standing=team.standing,
            publication_status=team.publication_status,
            eligibility={
                "status": eligibility.status,
                "reason": eligibility.reason,
                "evidence": [public_evidence(e) for e in eligibility.evidence[:4]],
            },
            evidence_link=f"/v2/teams/{team.entry_id}/evidence",
            pokemon=pokemon,
            population_id=pop,
            catalog_revision=meta.catalog_revision,
            rules_id=meta.rules_id,
            starts_at=meta.starts_at,
            division_id=population_keys[team.entry_id].division_id,
            competition_id=meta.competition_id,
            match_formats=meta.match_formats,
            provenance=team_provenance,
            provenance_count=provenance_count,
            performance=Performance(
                **{k: v for k, v in rates.items() if k != "excluded_count"},
                excluded_outcomes=rates["excluded_count"],
                played=rates["wins"] + rates["losses"] + rates["ties"],
                percentage=performance_percentage(rates["wins"], rates["losses"], rates["ties"]),
            ),
        )
        data = view.model_dump(mode="json", by_alias=True)
        ordinal = input.event_id + ":" + team.entry_id
        row("TEAMS", ordinal, "team", data)
        row("REG#" + meta.rules_id, ordinal, "team_index", data)
        row("TEAM#" + team.entry_id, "DETAIL", "team_detail", data)
        row("PLAYER#" + (team.player_id or team.entry_id), order + ":" + ordinal, "history", data)
        rank = f"0:{team.standing:010d}" if team.standing is not None else "1:unknown"
        row("EVENT#" + input.event_id + "#STANDINGS", rank + ":" + team.entry_id, "standing", data)
        eligible = (
            cut.status == "verified_complete"
            and eligibility.status == "included"
            and team.publication_status == "complete"
        )
        if not eligible:
            continue
        coverage.eligible_complete_rosters += 1
        coverage.unknown_stat_points += sum(p.stat_points is None for p in team.pokemon)
        base = dict(
            event_id=input.event_id,
            entry_id=team.entry_id,
            population_id=pop,
            day=day,
            placement=team.standing,
        )
        for group in ("form", "species"):
            ids = identities(team, group)
            contributions.append(
                DailyContribution(**base, group_by=group, metric="roster", identity="*", count=1)
            )
            for ident in ids:
                contributions.append(
                    DailyContribution(
                        **base,
                        group_by=group,
                        metric="species",
                        identity=identity_text(ident),
                        count=1,
                    )
                )
            for size in (2, 3):
                for core in canonical_cores(ids, size=size):
                    contributions.append(
                        DailyContribution(
                            **base,
                            group_by=group,
                            metric=f"core{size}",
                            identity=core_text(core),
                            count=1,
                        )
                    )
            for mon in team.pokemon:
                ident = identity_text(
                    (mon.species_id, (mon.form_id or "base") if group == "form" else "base")
                )
                attrs = {
                    "ability": None if mon.ability_id is None else [mon.ability_id],
                    "nature": None if mon.nature_id is None else [mon.nature_id],
                    "stat_points": None
                    if mon.stat_points is None
                    else [mon.stat_points.canonical()],
                    "item": None
                    if mon.item.state == "unknown"
                    else [mon.item.item_id if mon.item.state == "held" else "empty"],
                    "move": mon.moves
                    if mon.moves is not None and mon.moves_complete is True
                    else None,
                    "registered_mega": mon.mega_variant_ids,
                }
                for attr, values in attrs.items():
                    contributions.append(
                        DailyContribution(
                            **base,
                            group_by=group,
                            metric=attr,
                            identity=ident,
                            denominator=int(values is not None),
                            known_count=int(values is not None),
                            unknown_count=int(values is None),
                            excluded_count=int(values is None),
                        )
                    )
                    for value in set(values or []):
                        contributions.append(
                            DailyContribution(
                                **base,
                                group_by=group,
                                metric=attr,
                                identity=ident,
                                value=value,
                                count=1,
                            )
                        )
                    if group == "form":
                        target = (
                            coverage.known_attributes
                            if values is not None
                            else coverage.unknown_attributes
                        )
                        target[attr] = target.get(attr, 0) + 1
    for contribution in associations:
        contributions.append(contribution)
    for match in tournament.matches:
        linked = (
            all(
                p in mapped and p in cut.participant_ids
                for p in (match.player1_id, match.player2_id)
            )
            and cut.status == "verified_complete"
        )
        view = MatchView(
            **match.model_dump(exclude={"table", "score", "evidence"}),
            linkage_status="linked" if linked else "unverified",
            provenance=[public_evidence(e) for e in match.evidence],
        )
        row(
            "EVENT#" + input.event_id + "#MATCHES",
            f"{match.phase or 0:04}:{match.round or 0:06}:"
            f"{match.bracket_label or ''}:{match.match_id}",
            "match",
            view.model_dump(mode="json"),
        )
    # Retain only source identities whose entry rejection is explicitly diagnosed.
    # A removed revision entry is not reconstructed by guessing from old rows.
    for index, raw_entry in enumerate(raw):
        if f"teams.{index}" not in rejected or not isinstance(raw_entry, dict):
            continue
        player = raw_entry.get("player")
        if not isinstance(player, str) or not player or "limitless:" + player in mapped:
            continue
        if sum(r.get("player") == player for r in raw if isinstance(r, dict)) != 1:
            continue
        player_id = "limitless:" + player
        entry_id = input.event_id + ":" + player
        eligibility = entry_eligibility(cut, player_id=player_id)
        refs = [public_evidence(e) for e in eligibility.evidence[:4]]
        placing = raw_entry.get("placing")
        view = TeamView(
            team_id=entry_id,
            event_id=input.event_id,
            player_id=player_id,
            player_name=raw_entry.get("name") if isinstance(raw_entry.get("name"), str) else None,
            standing=placing if type(placing) is int and placing > 0 else None,
            publication_status="absent",
            eligibility={
                "status": eligibility.status,
                "reason": eligibility.reason,
                "evidence": refs,
            },
            diagnostics=["Source roster rejected during normalization; no build reconstructed"],
            catalog_revision=meta.catalog_revision,
            rules_id=meta.rules_id,
            starts_at=meta.starts_at,
            division_id=meta.division_id,
            competition_id=meta.competition_id,
            match_formats=meta.match_formats,
        )
        data = view.model_dump(mode="json")
        invalid_population = population_for(meta)
        invalid_pid = population_id(invalid_population)
        populations[invalid_pid] = invalid_population
        contributions.append(
            DailyContribution(
                event_id=input.event_id,
                entry_id=entry_id,
                population_id=invalid_pid,
                day=day,
                placement=view.standing,
                metric="coverage:invalid",
                identity=entry_id,
                count=1,
                known_count=int(eligibility.status == "included"),
                unknown_count=int(eligibility.status == "unknown"),
                excluded_count=int(eligibility.status == "excluded"),
            )
        )
        ordinal = input.event_id + ":" + entry_id
        for section, key, kind in [
            ("TEAMS", ordinal, "team"),
            ("REG#" + meta.rules_id, ordinal, "team_index"),
            ("TEAM#" + entry_id, "DETAIL", "team_detail"),
            ("PLAYER#" + player_id, order + ":" + ordinal, "history"),
            (
                "EVENT#" + input.event_id + "#STANDINGS",
                (f"0:{view.standing:010d}" if view.standing is not None else "1:unknown")
                + ":"
                + entry_id,
                "standing",
            ),
        ]:
            row(section, key, kind, data)
        if eligibility.status != "included":
            field = (
                "excluded_membership" if eligibility.status == "excluded" else "unknown_membership"
            )
            setattr(coverage, field, getattr(coverage, field) + 1)
    for index, contribution in enumerate(contributions):
        c = contribution.model_dump(mode="json")
        section = (
            ("DAY#" + c["population_id"] + "#" + c["day"])
            if c["day"]
            else ("UNDATED#" + c["population_id"])
        )
        row(section, f"{input.event_id}:{contribution.entry_id}:{index:06d}", "contribution", c)
    if day:
        for pid in populations:
            row(
                "DAYS#" + pid,
                day.isoformat() + ":" + input.event_id,
                "day",
                {"day": day.isoformat()},
            )
    public_cut = PublicCut(
        policy_version=cut.policy_version,
        status=cut.status,
        phase_id=cut.phase_id,
        expected_participant_count=cut.expected_participant_count,
        participant_count=len(cut.participant_ids),
        evidence=[PublicEvidence(**public_evidence(e)) for e in cut.evidence],
        diagnostics=cut.diagnostics,
    )
    event = EventSummary(
        **meta.model_dump(exclude={"evidence"}),
        cut=public_cut,
        coverage=coverage,
        populations=populations,
        provenance=[public_evidence(e) for e in meta.evidence],
        links={
            "standings": f"/v2/tournaments/{input.event_id}/standings",
            "matches": f"/v2/tournaments/{input.event_id}/matches",
        },
    )
    row("EVENTS", order + ":" + input.event_id, "event", event.model_dump(mode="json"))
    row("EVENT#" + input.event_id, "DETAIL", "event_detail", event.model_dump(mode="json"))
    for pid, population in populations.items():
        row(
            "POPULATIONS",
            pid + ":" + input.event_id,
            "population",
            population.model_dump(mode="json"),
            event_day=day.isoformat() if day else None,
        )
    return PublicEventBundle(
        input=input,
        cut=cut,
        populations=populations,
        rows=sorted(rows, key=lambda r: (r["section"], r["ordinal"])),
    )
