"""Real cut proof and synthetic conservative-policy counterexamples."""

import hashlib
import json
from collections import Counter
from pathlib import Path

import pytest

from ingestion.normalize import normalize_capture
from tests.source_helpers import real_inputs

GAMBIT = {
    "azelle",
    "martinb",
    "nanamixd",
    "wisdomlordraphael",
    "imalbe",
    "happy_hannya",
    "giac28",
    "nhannq2509",
}


def inputs(second=False):
    from scripts.build_phase2_fixtures import assemble_event

    capture, admission, catalog = real_inputs("limitless")
    if second:
        capture, _ = assemble_event("6abe6180880ed327106e0cf4")
    return capture, normalize_capture(capture, admission=admission, catalog=catalog)


def decide(capture, result):
    from core.top_cut import derive_cut

    return derive_cut(capture, result)


def mutate(capture, key, change):
    """Synthetic mutation, with an honest new UTF-8 hash."""
    capture = capture.model_copy(deep=True)
    body = json.loads(capture.documents[key].body)
    change(body)
    capture.documents[key].body = json.dumps(body)
    capture.documents[key].content_hash = hashlib.sha256(
        capture.documents[key].body.encode()
    ).hexdigest()
    return capture


def test_real_gambit_cut_is_eight_not_all_standings():
    capture, result = inputs()
    decision = decide(capture, result)
    assert result.admission_status == "verified"
    assert len(result.tournament.teams) == 31
    assert decision.status == "verified_complete"
    assert set(decision.participant_ids) == {"limitless:" + p for p in GAMBIT}
    assert decision.phase_id == 2
    assert decision.expected_participant_count == 8
    assert {e.document_key for e in decision.evidence} == {"details", "pairings", "standings"}
    for evidence in decision.evidence:
        assert capture.documents[evidence.document_key].content_hash == evidence.content_hash
    assert any("phases" in e.path for e in decision.evidence)
    assert any("[" in e.path for e in decision.evidence)
    # Removing all ranks cannot alter bracket proof.
    capture = mutate(capture, "standings", lambda rows: [r.pop("placing", None) for r in rows])
    _, admission, catalog = real_inputs("limitless")
    assert (
        decide(capture, normalize_capture(capture, admission=admission, catalog=catalog)).status
        == "verified_complete"
    )


def test_second_real_cut_is_two_with_original_capture_integrity():
    from scripts.build_phase2_fixtures import assemble_event

    capture, report = assemble_event("6abe6180880ed327106e0cf4")
    assert len(report) == 3
    assert capture.fetched_at.isoformat() == max(r["captured_at"] for r in report)
    for row in report:
        meta = json.loads(Path(row["metadata_path"]).read_text())
        body = Path(row["body_path"]).read_bytes()
        assert hashlib.sha256(body).hexdigest() == meta["sha256"] == row["sha256"]
    capture, result = inputs(second=True)
    assert result.admission_status == "verified"
    assert len(result.tournament.teams) == 6
    decision = decide(capture, result)
    assert decision.status == "verified_complete"
    assert decision.participant_ids == ["limitless:axtheb", "limitless:red5"]
    assert decision.expected_participant_count == 2


@pytest.mark.parametrize(
    "case,status",
    [
        ("swiss", "none"),
        ("no_structure", "unknown"),
        ("missing_node", "verified_partial"),
        ("labels", "unknown"),
        ("duplicate", "unknown"),
        ("phases", "unknown"),
        ("progression", "unknown"),
        ("mapping", "verified_partial"),
        ("tie", "verified_partial"),
        ("double_loss", "verified_partial"),
        ("pending", "verified_partial"),
        ("bye", "verified_partial"),
    ],
)
def test_synthetic_incomplete_or_conflicting_evidence_never_certifies(case, status):
    capture, _ = inputs()
    if case in {"swiss", "no_structure", "phases"}:

        def change(body):
            if case == "swiss":
                body["phases"] = body["phases"][:1]
            elif case == "no_structure":
                body.pop("phases")
            else:
                body["phases"].append(body["phases"][1])

        capture = mutate(capture, "details", change)
        if case == "swiss":
            capture = mutate(
                capture,
                "pairings",
                lambda rows: rows.__setitem__(slice(None), [r for r in rows if r["phase"] == 1]),
            )
    elif case == "mapping":
        capture = mutate(
            capture,
            "standings",
            lambda rows: rows.__setitem__(
                slice(None), [r for r in rows if r["player"] != "azelle"]
            ),
        )
    else:

        def change(rows):
            node = next(r for r in rows if r.get("match") == "T8-1")
            if case == "missing_node":
                rows.remove(node)
            elif case == "labels":
                for r in rows:
                    if r["phase"] == 2:
                        r["match"] = "unsupported"
            elif case == "duplicate":
                rows.append(dict(node))
            elif case == "progression":
                node["winner"] = "imalbe"
            elif case == "bye":
                node.pop("player2")
            else:
                node["winner"] = {"tie": 0, "double_loss": -1, "pending": None}[case]

        capture = mutate(capture, "pairings", change)
    _, admission, catalog = real_inputs("limitless")
    result = normalize_capture(capture, admission=admission, catalog=catalog)
    decision = decide(capture, result)
    assert decision.status == status
    assert decision.diagnostics or status == "none"


def test_invalid_individual_roster_does_not_erase_cut_identity():
    capture, _ = inputs()

    def change(rows):
        row = next(r for r in rows if r["player"] == "azelle")
        row["decklist"][0]["id"] = "invented"

    capture = mutate(capture, "standings", change)
    _, admission, catalog = real_inputs("limitless")
    result = normalize_capture(capture, admission=admission, catalog=catalog)
    assert decide(capture, result).status == "verified_complete"
    assert len(result.tournament.teams) == 31
    assert (
        next(
            t for t in result.tournament.teams if t.player_id == "limitless:azelle"
        ).publication_status
        == "partial"
    )


def test_membership_and_unknown_policy():
    from core.top_cut import derive_cut, entry_eligibility

    capture, result = inputs()
    cut = derive_cut(capture, result)
    assert entry_eligibility(cut, player_id="limitless:azelle").status == "included"
    assert entry_eligibility(cut, player_id="limitless:other").status == "excluded"
    assert entry_eligibility(cut, player_id=None).status == "unknown"
    with pytest.raises(ValueError, match="policy"):
        derive_cut(capture, result, policy_version="invented")


def test_raw_leaf_expectations_are_independent():
    capture, _ = inputs()
    rows = json.loads(capture.documents["pairings"].body)
    leaves = [r for r in rows if str(r.get("match", "")).startswith("T8-")]
    assert Counter(p for row in leaves for p in [row["player1"], row["player2"]]) == Counter(
        {p: 1 for p in GAMBIT}
    )
