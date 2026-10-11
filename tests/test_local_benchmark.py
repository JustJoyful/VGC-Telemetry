from decimal import Decimal

import pytest


def test_dynamodb_sizes_count_utf8_names_nested_overhead_and_binary():
    from scripts.benchmarks.local import attribute_size, item_sizes

    assert attribute_size({"S": "é"}) == 2
    assert attribute_size({"B": b"123"}) == 3
    assert attribute_size({"BOOL": True}) == 1
    assert attribute_size({"L": [{"S": "x"}, {"NULL": True}]}) == 7
    assert attribute_size({"M": {"é": {"S": "x"}}}) == 7
    sizes = item_sizes({"PK": "a", "SK": "b", "n": Decimal("1234")})
    assert sizes["stored_attribute_bytes"] == 10
    assert sizes["storage_overhead_bytes"] == 100
    assert sizes["wire_json_bytes"] > sizes["stored_attribute_bytes"]


def test_benchmark_schema_rejects_missing_provenance():
    from pydantic import ValidationError

    from scripts.benchmarks.local import BenchmarkReport

    with pytest.raises(ValidationError):
        BenchmarkReport.model_validate({"schema_version": 1})


def test_number_size_preserves_all_38_significant_digits():
    from scripts.benchmarks.local import attribute_size

    assert attribute_size({"N": "12345678901234567890123456789012345678"}) == 20
    assert attribute_size({"N": "10000000000000000000000000000000000000"}) == 2


@pytest.mark.parametrize("changed", ["app/main.py", "tests/fixtures/evidence.json"])
def test_executed_provenance_rejects_stale_source_or_inputs(tmp_path, changed):
    from scripts.benchmarks.local import source_identity, verify_identity

    for name in (
        "Dockerfile.local",
        "Dockerfile.local.dockerignore",
        ".dockerignore",
        "compose.yaml",
        "uv.lock",
        "pyproject.toml",
        "app/main.py",
        "tests/fixtures/evidence.json",
    ):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("original")
    identity = source_identity(tmp_path)
    expected = {"source_tree_sha256": identity[0], "inputs": identity[1]}
    verify_identity(expected, identity)
    (tmp_path / changed).write_text("changed")
    with pytest.raises(ValueError, match="Executed source/input identity"):
        verify_identity(expected, source_identity(tmp_path))
