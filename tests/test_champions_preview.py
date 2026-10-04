import importlib
import importlib.util
import subprocess
import sys

from ingestion.normalize import normalize_capture
from tests.source_helpers import real_inputs


def api():
    assert importlib.util.find_spec("ingestion.preview"), "Read-back preview is missing"
    return importlib.import_module("ingestion.preview")


def test_preview_reports_observations_and_unknown_private_fields():
    c, a, k = real_inputs("limitless")
    report = api().build_preview(normalize_capture(c, admission=a, catalog=k))
    assert report.admission_status == "verified" and report.observed_entrants == 31
    assert report.complete_rosters == 31 and report.eligible_rosters == 31
    assert report.unknown_fields["stat_points"] == 31 * 6
    assert report.unknown_fields["item"] == 1
    assert report.tournament.teams[0].pokemon[0].nature_id == "modest"
    assert "usage_percentage" not in report.model_dump()


def test_pending_withholds_analytic_eligibility():
    c, a, k = real_inputs("limitless")
    result = normalize_capture(
        c, admission=a, catalog=k.model_copy(update={"verification_status": "pending"})
    )
    preview = api().build_preview(result)
    assert preview.admission_status == "pending" and preview.eligible_rosters == 0
    assert preview.diagnostics[0].code == "catalog_pending"


def test_preview_cli_and_invalid_input(tmp_path):
    module = api()
    output = tmp_path / "preview.json"
    args = [
        sys.executable,
        "-m",
        "ingestion.preview",
        "--capture",
        "tests/fixtures/sources/limitless/accepted-capture.json",
        "--admission",
        "core/sources/admission.json",
        "--catalog-index",
        "core/regulations/v2/index.json",
        "--output",
        str(output),
    ]
    result = subprocess.run(args, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert module.PreviewReport.model_validate_json(output.read_text()).complete_rosters == 31
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{bad")
    args[args.index("--capture") + 1] = str(invalid)
    assert subprocess.run(args, capture_output=True, text=True, timeout=20).returncode != 0
