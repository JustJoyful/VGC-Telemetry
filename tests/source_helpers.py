from pathlib import Path

from core.catalog import load_catalog
from core.models.champions import SourceCapture
from core.source_admission import load_admissions

INDEX = Path("core/regulations/v2/index.json")


def real_inputs(source, *, fixture="accepted-capture.json"):
    capture = SourceCapture.model_validate_json(
        Path(f"tests/fixtures/sources/{source}/{fixture}").read_text()
    )
    admission = next(
        a for a in load_admissions(Path("core/sources/admission.json")) if a.source == source
    )
    return capture, admission, load_catalog("champions-mc-3661ce4", index_path=INDEX)
