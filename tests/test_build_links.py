import hashlib
import json

import boto3
import pytest

from core.models.champions import CapturedDocument, ChampionsTournament


def source():
    from core.models.builds import BuildSource

    body = "Published builds may be read through this public JSON export."
    doc = CapturedDocument(
        url="https://example.test/access",
        content_type="text/plain",
        body=body,
        content_hash=hashlib.sha256(body.encode()).hexdigest(),
    )
    return BuildSource(
        source="synthetic-public",
        public=True,
        reviewed_by="reviewer",
        access_document=doc,
        access_quote=body,
        access_captured_at="2026-10-10T00:00:00Z",
    )


def build(*, player="player-1", event="event-1", pokemon=None, supersedes=None):
    from scraper.builds import normalize_public_build

    body = json.dumps(
        {
            "schema": "published-build-v1",
            "build_id": "build-1",
            "author": "Author",
            "event_source": "limitless",
            "event_id": event,
            "player_id": player,
            "pokemon": pokemon or [{"species_id": "gengar", "nature_id": "timid"}],
            "supersedes": supersedes,
        }
    )
    document = CapturedDocument(
        url="https://example.test/build/1",
        content_type="application/json",
        body=body,
        content_hash=hashlib.sha256(body.encode()).hexdigest(),
    )
    return normalize_public_build(document, source=source(), captured_at="2026-10-10T00:00:00Z")


def event(*, player="player-1", event_id="event-1", species="gengar", nature=None):
    return ChampionsTournament(
        metadata={"event_id": "limitless:" + event_id},
        teams=[
            {
                "entry_id": "entry-1",
                "player_id": player,
                "player_name": "Same name",
                "publication_status": "partial",
                "pokemon": [{"species_id": species, "nature_id": nature}],
            }
        ],
    )


def link(build_record, tournaments, **kwargs):
    from ingestion.build_links import resolve_build

    return resolve_build(build_record, tournaments, **kwargs)


def test_exact_unique_partial_evidence_and_duplicates():
    record, tournament = build(), event()
    result = link(record, [tournament])
    assert result.status == "linked" and result.entry_id == "entry-1"
    assert record.pokemon[0].stat_points is None
    assert record.pokemon[0].field_evidence["nature_id"][0].url == record.evidence.url
    assert record == build()
    assert tournament.teams[0].pokemon[0].nature_id is None


@pytest.mark.parametrize(
    "tournaments,status,reason",
    [
        ([event(player="player-2")], "unlinked", "no_exact_identity"),
        ([event(event_id="event-2")], "unlinked", "no_exact_identity"),
        ([event(species="pikachu")], "pending_conflict", "roster_mismatch"),
        ([event(), event()], "pending_conflict", "ambiguous_identity"),
        ([event(nature="modest")], "pending_conflict", "observed_field_conflict"),
    ],
)
def test_mismatch_same_name_ambiguity_and_observed_conflicts(tournaments, status, reason):
    result = link(build(), tournaments)
    assert result.status == status and reason in result.diagnostics


def test_supplementary_conflict_no_source_priority_or_recency():
    first = build()
    second = build(pokemon=[{"species_id": "gengar", "nature_id": "modest"}])
    result = link(second, [event()], previous_builds=[first])
    assert (
        result.status == "pending_conflict" and "supplementary_field_conflict" in result.diagnostics
    )
    assert link(first, [event()], previous_builds=[first]).status == "linked"


def test_explicit_reviewed_cross_reference_requires_roster_and_evidence():
    from core.models.builds import BuildCrossReference

    record = build(player="external-player", event="external-event")
    cross = BuildCrossReference(
        source=record.source,
        source_event_id="external-event",
        source_player_id="external-player",
        target_source="limitless",
        target_event_id="event-1",
        target_entry_id="entry-1",
        reviewer="reviewer",
        reason="Exact source correspondence",
        evidence=[record.evidence],
    )
    assert link(record, [event()], cross_reference=cross).status == "linked"
    assert (
        link(record, [event(species="pikachu")], cross_reference=cross).status == "pending_conflict"
    )


def test_revision_store_corrections_counts_unchanged_and_bounded_query(dynamodb_table):
    from db.builds import BuildStore

    s3 = boto3.client("s3")
    s3.create_bucket(Bucket="build-test")
    store = BuildStore(table=dynamodb_table, s3_client=s3, bucket="build-test")
    original = build()
    tournament = event()
    initial = tournament.model_dump()
    result = store.ingest(original, [tournament])
    assert result.status == "linked"
    assert store.ingest(original, [tournament]) == result
    conflict = build(pokemon=[{"species_id": "gengar", "nature_id": "modest"}])
    assert store.ingest(conflict, [tournament]).status == "pending_conflict"
    correction = build(
        pokemon=[{"species_id": "gengar", "nature_id": "modest"}], supersedes=original.revision_id
    )
    assert store.ingest(correction, [tournament]).status == "linked"
    later = build(
        pokemon=[{"species_id": "gengar", "nature_id": "jolly"}], supersedes=correction.revision_id
    )
    assert store.ingest(later, [tournament]).status == "linked"
    assert store.load(original.revision_id) == original
    assert store.load(correction.revision_id) == correction
    assert tournament.model_dump() == initial
    page = store.links("limitless", "limitless:event-1", "entry-1", limit=1)
    assert len(page["Items"]) == 1 and page["LastEvaluatedKey"]
    with pytest.raises(ValueError):
        store.links("limitless", "limitless:event-1", "entry-1", limit=101)
    assert all(
        o["Key"].startswith("staging/v2/builds/")
        for o in s3.list_objects_v2(Bucket="build-test")["Contents"]
    )


def test_correction_cannot_claim_another_author_or_source():
    old = build()
    correction = build(
        pokemon=[{"species_id": "gengar", "nature_id": "modest"}], supersedes=old.revision_id
    )
    forged = correction.model_copy(update={"author": "Different author"})
    assert "invalid_correction" in link(forged, [event()], previous_builds=[old]).diagnostics


def test_access_evidence_gate_and_payload_identity():
    from scraper.builds import normalize_public_build

    admitted = source()
    with pytest.raises(ValueError):
        source().model_copy(update={"public": False}).validate_access()
    document = build().document
    with pytest.raises(ValueError):
        normalize_public_build(
            document.model_copy(update={"content_hash": "0" * 64}),
            source=admitted,
            captured_at="2026-10-10T00:00:00Z",
        )


def test_partial_revision_cannot_erase_prior_fields(dynamodb_table):
    from db.builds import BuildStore

    s3 = boto3.client("s3")
    s3.create_bucket(Bucket="partial-build-test")
    store = BuildStore(table=dynamodb_table, s3_client=s3, bucket="partial-build-test")
    assert store.ingest(build(), [event()]).status == "linked"
    partial = build(pokemon=[{"species_id": "gengar"}])
    assert store.ingest(partial, [event()]).status == "pending_conflict"
    conflicting = build(pokemon=[{"species_id": "gengar", "nature_id": "modest"}])
    assert store.ingest(conflicting, [event()]).status == "pending_conflict"


def test_pending_build_conflicts_remain_visible_to_later_sources(dynamodb_table):
    from core.build_evidence import normalize_public_build
    from db.builds import BuildStore

    s3 = boto3.client("s3")
    s3.create_bucket(Bucket="conflict-build-test")
    store = BuildStore(table=dynamodb_table, s3_client=s3, bucket="conflict-build-test")
    first = build()
    store.ingest(first, [event()])
    conflicting = build(pokemon=[{"species_id": "gengar", "nature_id": "modest"}])
    assert store.ingest(conflicting, [event()]).status == "pending_conflict"
    other_source = source().model_copy(update={"source": "other-reviewed-source"})
    third = normalize_public_build(
        first.document, source=other_source, captured_at=first.evidence.captured_at
    )
    assert store.ingest(third, [event()]).status == "pending_conflict"


def test_same_build_revalidated_for_corrected_event_revision(dynamodb_table):
    from db.builds import BuildStore
    from db.schema_v2 import event_key

    s3 = boto3.client("s3")
    s3.create_bucket(Bucket="event-build-test")
    store = BuildStore(table=dynamodb_table, s3_client=s3, bucket="event-build-test")
    event_id = "limitless:event-1"
    dynamodb_table.put_item(Item={**event_key(event_id), "revision_id": "old"})
    old = store.ingest(build(), [event()], event_revisions={event_id: "old"})
    assert old.status == "linked"
    dynamodb_table.put_item(Item={**event_key(event_id), "revision_id": "corrected"})
    result = store.ingest(
        build(), [event(nature="modest")], event_revisions={event_id: "corrected"}
    )
    assert result.status == "pending_conflict" and result.event_revision == "corrected"
    assert len(store.links("limitless", event_id, "entry-1")["Items"]) == 2
