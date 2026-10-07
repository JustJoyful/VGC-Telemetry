"""Conservative retained-evidence Limitless elimination-tree policy.

No standings threshold, rounds count, or roster validity can establish membership.
"""

import json
import re
from collections import Counter

from core.models.champions import NormalizationResult, SourceCapture
from core.models.top_cut import CutDecision, CutEvidence, EntryEligibility
from core.source_admission import document_evidence

POLICY_VERSION = "limitless-single-bracket-v1"


def derive_cut(
    capture: SourceCapture,
    result: NormalizationResult,
    *,
    policy_version: str = POLICY_VERSION,
) -> CutDecision:
    if policy_version != POLICY_VERSION:
        raise ValueError("Unsupported cut policy version")
    # Revalidate even callers using mutable model copies.
    capture = SourceCapture.model_validate(capture.model_dump(mode="json"))
    decision = CutDecision(policy_version=policy_version, status="unknown")

    def evidence(key, path):
        decision.evidence.append(
            CutEvidence(**document_evidence(capture, key).model_dump(), path=path)
        )

    def unknown(reason):
        decision.diagnostics.append(reason)
        return decision

    if result.admission_status != "verified" or result.tournament is None:
        return unknown("Event is not admitted Champions doubles")
    if result.tournament.metadata.event_id != f"limitless:{capture.source_event_id}":
        return unknown("Event identity mismatch")
    if any(
        k not in capture.documents or capture.documents[k].status != 200
        for k in ("details", "standings", "pairings")
    ):
        return unknown("Required source documents unavailable")
    details = json.loads(capture.documents["details"].body)
    pairings = json.loads(capture.documents["pairings"].body)
    standings = json.loads(capture.documents["standings"].body)
    if (
        not isinstance(details, dict)
        or not isinstance(pairings, list)
        or not isinstance(standings, list)
    ):
        return unknown("Invalid source document shape")
    if any(not isinstance(row, dict) for row in pairings):
        return unknown("Invalid source pairing shape")
    evidence("details", "$.phases")
    phases = details.get("phases")
    if not isinstance(phases, list) or not phases:
        return unknown("Missing explicit structure")
    phase_ids = [p.get("phase") for p in phases if isinstance(p, dict)]
    if (
        len(phase_ids) != len(phases)
        or any(type(p) is not int or p < 1 for p in phase_ids)
        or len(set(phase_ids)) != len(phase_ids)
    ):
        return unknown("Conflicting or invalid phase IDs")
    if all(p.get("type") == "SWISS" for p in phases):
        if any(r.get("phase") not in phase_ids or r.get("match") for r in pairings):
            return unknown("Pairings contradict Swiss-only structure")
        decision.status = "none"
        return decision
    if (
        len(phases) != 2
        or phases[0].get("type") != "SWISS"
        or phases[1].get("type") != "SINGLE_BRACKET"
        or phase_ids[0] >= phase_ids[1]
    ):
        return unknown("Unsupported or ambiguous bracket/division structure")
    decision.phase_id = phase_ids[1]
    nodes = {}
    participants = set()
    incomplete = []
    normalized = {
        m.bracket_label: m for m in result.tournament.matches if m.phase == decision.phase_id
    }
    for index, row in enumerate(pairings):
        if row.get("phase") != decision.phase_id:
            if row.get("phase") not in phase_ids:
                return unknown("Pairing has undeclared phase")
            continue
        evidence("pairings", f"$[{index}]")
        label = row.get("match")
        parsed = re.fullmatch(r"T(2|4|8|16|32|64|128|256)-([1-9][0-9]*)", str(label))
        if not parsed:
            return unknown("Unsupported bracket label")
        size, ordinal = map(int, parsed.groups())
        if ordinal > size // 2 or (size, ordinal) in nodes:
            return unknown("Duplicate or invalid bracket label")
        nodes[size, ordinal] = row
        p1, p2, winner = row.get("player1"), row.get("player2"), row.get("winner")
        valid_players = [p for p in (p1, p2) if isinstance(p, str) and p.strip()]
        participants.update(valid_players)
        match = normalized.get(label)
        if (
            len(valid_players) != 2
            or p1 == p2
            or not isinstance(winner, str)
            or winner not in valid_players
        ):
            incomplete.append(f"Unresolved/non-played bracket node {label}")
        elif (
            match is None
            or match.outcome != "win"
            or (match.player1_id, match.player2_id, match.winner_id)
            != tuple("limitless:" + p for p in (p1, p2, winner))
        ):
            incomplete.append(f"Missing/conflicting normalized match {label}")
    if not nodes:
        return unknown("No bracket nodes captured")
    largest = max(size for size, _ in nodes)
    decision.expected_participant_count = largest
    expected = {
        (size, i)
        for size in (2, 4, 8, 16, 32, 64, 128, 256)
        if size <= largest
        for i in range(1, size // 2 + 1)
    }
    if set(nodes) != expected:
        incomplete.append("Incomplete elimination tree")
    leaves = [
        p
        for (n, _), row in nodes.items()
        if n == largest
        for p in (row.get("player1"), row.get("player2"))
        if isinstance(p, str)
    ]
    if len(leaves) != len(set(leaves)) or len(participants) > largest:
        return unknown("Repeated/conflicting bracket entrant")
    for (size, i), row in nodes.items():
        if size == largest:
            continue
        children = [nodes.get((size * 2, i * 2 - 1)), nodes.get((size * 2, i * 2))]
        if all(children) and all(isinstance(c.get("winner"), str) for c in children):
            if all(isinstance(row.get(key), str) for key in ("player1", "player2")) and {
                c["winner"] for c in children
            } != {row.get("player1"), row.get("player2")}:
                return unknown("Winner progression conflicts with parent participants")
    scalar_standings = [
        r
        for r in standings
        if isinstance(r, dict) and isinstance(r.get("player"), str) and r["player"].strip()
    ]
    if len(scalar_standings) != len(standings):
        decision.diagnostics.append("Malformed source standings identity retained as a diagnostic")
    raw_links = Counter(r["player"] for r in scalar_standings)
    ranks = {r["player"]: r.get("placing") for r in scalar_standings}
    evidence("standings", "$[*].player/placing")
    if any(raw_links[p] != 1 for p in participants):
        incomplete.append("Missing or ambiguous standings participant mapping")
    normalized_links = Counter(t.player_id for t in result.tournament.teams if t.player_id)
    if any(normalized_links["limitless:" + p] > 1 for p in participants):
        return unknown("Duplicate normalized player-to-entry linkage")
    for row in nodes.values():
        winner = row.get("winner")
        if not isinstance(winner, str):
            continue
        for loser in (row.get("player1"), row.get("player2")):
            winner_rank, loser_rank = ranks.get(winner), ranks.get(loser)
            if (
                loser != winner
                and type(winner_rank) is int
                and type(loser_rank) is int
                and winner_rank >= loser_rank
            ):
                return unknown("Standings contradict resolved bracket results")
    decision.participant_ids = sorted("limitless:" + p for p in participants)
    decision.status = "verified_partial" if incomplete else "verified_complete"
    decision.diagnostics.extend(incomplete)
    return decision


def entry_eligibility(decision: CutDecision, *, player_id: str | None) -> EntryEligibility:
    if player_id is None:
        status, reason = "unknown", "No source-scoped player identity"
    elif player_id in decision.participant_ids and decision.status in (
        "verified_complete",
        "verified_partial",
    ):
        status, reason = "included", "Explicit bracket participant"
    elif decision.status in ("verified_complete", "none"):
        status, reason = "excluded", "Outside proven cut population"
    else:
        status, reason = "unknown", "Cut completeness unverified"
    return EntryEligibility(status=status, reason=reason, evidence=decision.evidence)
