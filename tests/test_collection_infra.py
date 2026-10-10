import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "infra"


def test_schedules_are_explicit_and_disabled_by_default():
    module = (ROOT / "modules/eventbridge_scheduler/main.tf").read_text()
    variables = (ROOT / "modules/eventbridge_scheduler/variables.tf").read_text()
    assert all(
        re.search(rf"\b{mode}\s+=", module)
        for mode in ("discover", "recent", "reference", "reconcile")
    )
    assert "recover = true" in module and 'mode = "native"' in module
    assert module.count("is_enabled          = var.source_admission_verified") == 2
    assert "default = false" in variables


def test_worker_permissions_are_scoped():
    iam = (ROOT / "modules/iam/main.tf").read_text()
    assert "dynamodb:Scan" not in iam
    assert '"V2#COLLECT#*"' in iam
    assert '"V2#COLLECTION_WORK#limitless"' in iam
    assert "${var.bucket_arn}/staging/v2/captures/*" in iam
    assert "${var.bucket_arn}/staging/v2/normalized/*" in iam
    assert "dynamodb:DeleteItem" in iam
    assert "lambda:InvokeFunction" in iam


def test_both_environments_pass_worker_configuration():
    for environment in ("dev", "prod"):
        main = (ROOT / f"environments/{environment}/main.tf").read_text()
        for name in (
            "source_admission_verified",
            "collection_page_size",
            "collection_batch_size",
            "recent_days",
            "reconcile_days",
        ):
            assert re.search(rf"\b{name}\s+=\s+var\.{name}\b", main)
