import importlib
import importlib.util

from tests.champions_helpers import admission_data, capture


def classify(details):
    assert importlib.util.find_spec("core.source_admission"), "Source admission is missing"
    api = importlib.import_module("core.source_admission")
    return api.classify_event(capture(details=details), api.SourceAdmission(**admission_data()))


def test_generic_vgc_name_and_date_do_not_prove_champions():
    result = classify({"game": "VGC", "name": "Champions", "date": "2026-10-01T00:00:00Z"})
    assert result.status == "pending"
    assert result.metadata is None or result.metadata.catalog_revision is None


def test_verified_mapping_selects_event_rules_while_collection_disabled():
    result = classify(
        {"id": "event", "game": "VGC", "format": "M-C", "platform": "SWITCH", "name": "Cup"}
    )
    assert result.status == "verified"
    assert result.metadata.catalog_revision == "test-mc"
    assert result.metadata.starts_at is None
    assert result.metadata.entrant_count is None


def test_legacy_and_conflicting_platform_are_rejected():
    for details in [
        {"game": "VGC", "format": "SVI", "platform": "SWITCH"},
        {"game": "VGC", "format": "M-C", "platform": "OTHER"},
        {"game": "PTCG", "format": "M-C", "platform": "SWITCH"},
    ]:
        assert classify(details).status != "verified"


def test_custom_rules_are_not_mixed_with_standard_regulation():
    result = classify(
        {"game": "VGC", "format": "M-C", "platform": "SWITCH", "specialRules": ["off-meta-only"]}
    )
    assert result.status == "pending"
    assert any(d.code == "custom_rules" for d in result.diagnostics)


def test_capture_resource_must_match_source_and_event():
    from core.source_admission import SourceAdmission, classify_event
    from tests.champions_helpers import admission_data, capture

    original = capture()
    doc = original.documents["details"].model_copy(update={"url": "https://evil.test/fake"})
    forged = original.model_copy(update={"documents": original.documents | {"details": doc}})
    result = classify_event(forged, SourceAdmission(**admission_data()))
    assert result.status == "rejected" and result.diagnostics[0].code == "capture_origin_conflict"
