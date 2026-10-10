import subprocess
import sys
from pathlib import Path


def test_catalog_and_build_iam_are_exact_and_packaged():
    iam = Path("infra/modules/iam/main.tf").read_text()
    assert '"V2#CATALOG"' in iam
    assert '"V2#BUILD*"' in iam
    assert "${var.bucket_arn}/staging/v2/catalog/*" in iam
    assert "${var.bucket_arn}/staging/v2/builds/*" in iam
    assert "dynamodb:Scan" not in iam
    assert (
        iam.split('resource "aws_iam_role_policy" "scraper_policy"')[1].count(
            "dynamodb:ConditionCheckItem"
        )
        == 1
    )
    assert (
        "dynamodb:ConditionCheckItem"
        in iam.split('resource "aws_iam_role_policy" "ingestion_policy"')[1].split(
            'resource "aws_iam_role" "scraper_role"'
        )[0]
    )
    docker = Path("Dockerfile").read_text()
    assert "COPY db/ ${LAMBDA_TASK_ROOT}/db/" in docker.split("AS scraper")[1]
    assert "COPY ingestion/ /var/task/ingestion/" in docker.split("AS ingestion")[0]
    assert (
        "COPY app/ ${LAMBDA_TASK_ROOT}/app/"
        in docker.split("AS ingestion")[1].split("AS scraper")[0]
    )


def test_runtime_read_configuration_wired_without_source_enablement():
    module = Path("infra/modules/lambda_api/main.tf").read_text()
    assert "CATALOG_RUNTIME_ENABLED" in module and "S3_RAW_BUCKET_NAME" in module
    for name in ("dev", "prod"):
        main = Path(f"infra/environments/{name}/main.tf").read_text()
        assert "catalog_runtime_enabled" in main
        variables = Path(f"infra/environments/{name}/variables.tf").read_text()
        assert "catalog_runtime_enabled" in variables


def test_api_and_native_imports_do_not_require_scraper_package():
    code = """
import importlib.abc
import sys
class NoScraper(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "scraper" or fullname.startswith("scraper."):
            raise ModuleNotFoundError("Scraper is absent in API/ingestion images")
sys.meta_path.insert(0, NoScraper())
import app.main
import ingestion.handler
import ingestion.native_handler
import ingestion.build_handler
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=20
    )
    assert result.returncode == 0, result.stderr
