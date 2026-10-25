import hashlib
import io
import json
import tarfile
from unittest.mock import Mock

import pytest


@pytest.mark.parametrize("unsafe", [False, True])
def test_restore_rejects_bad_archives_before_docker(tmp_path, monkeypatch, unsafe):
    from scripts.local import stack

    hashes = {}
    for suffix in stack.VOLUMES:
        path = tmp_path / f"{suffix}.tar"
        with tarfile.open(path, "w") as stream:
            item = tarfile.TarInfo("../escape" if unsafe else "valid")
            item.size = 1
            stream.addfile(item, io.BytesIO(b"x"))
        hashes[suffix] = hashlib.sha256(path.read_bytes()).hexdigest() if unsafe else "wrong"
    (tmp_path / "manifest.json").write_text(json.dumps({"schema_version": 1, "sha256": hashes}))
    docker = Mock(side_effect=AssertionError("Docker ran before archive validation"))
    monkeypatch.setattr(stack, "command", docker)
    with pytest.raises(ValueError, match="Unsafe archive|checksum"):
        stack.main(["--project", "vgc-phase4a-restore", "restore", "--input", str(tmp_path)])
    docker.assert_not_called()


def test_reset_requires_confirmation_before_docker(monkeypatch):
    from scripts.local import stack

    docker = Mock(side_effect=AssertionError("Docker ran before confirmation"))
    monkeypatch.setattr(stack, "command", docker)
    with pytest.raises(SystemExit):
        stack.main(["reset-demo"])
    docker.assert_not_called()


def test_host_helper_ignores_ambient_compose_profile_and_file(monkeypatch):
    from scripts.local import stack

    monkeypatch.setenv("COMPOSE_PROFILES", "tunnel")
    monkeypatch.setenv("COMPOSE_FILE", "/tmp/unrelated.yaml")
    run = Mock()
    monkeypatch.setattr(stack.subprocess, "run", run)
    stack.command("docker", "compose", "-p", "vgc-phase4a", "up", "-d")
    assert run.call_args.kwargs["env"]["COMPOSE_PROFILES"] == ""
    assert str(stack.ROOT / "compose.yaml") in run.call_args.args[0]


def test_readme_documents_live_refresh_and_offline_commands():
    from scripts.local.stack import ROOT

    readme = (ROOT / "README.md").read_text()
    for command in ("stack.py live", "stack.py refresh", "stack.py up", "stack.py seed-demo"):
        assert f"python3 scripts/local/{command}" in readme
    assert "never pushed to GitHub" in readme
    assert "docker compose down -v" in readme


def test_safe_seed_runs_in_container_without_reset(tmp_path, monkeypatch, capsys):
    from scripts.local import stack

    calls = []

    def command(*args, **kwargs):
        calls.append(args)
        return '{"dataset":"retained-fixtures","seeded":true}' if kwargs.get("capture") else None

    monkeypatch.setattr(stack, "command", command)
    output = tmp_path / "seed.json"
    stack.main(["--project", "vgc-phase5", "seed-demo", "--output", str(output)])
    assert json.loads(output.read_text())["seeded"] is True
    assert any("exec" in c and "scripts.local.cli" in c and "seed-demo" in c for c in calls)
    assert not any("--confirm-reset" in c or "reset-demo" in c for c in calls)
    assert calls[-1][-3:] == ("start", "worker", "scheduler")


def test_compose_profiles_isolate_public_routes_and_leave_admission_disabled():
    import yaml

    from scripts.local.stack import ROOT

    config = yaml.safe_load((ROOT / "compose.yaml").read_text())
    services = config["services"]
    for name in ["dynamodb", "storage", "worker", "scheduler", "bootstrap"]:
        assert not services[name].get("ports")
    assert services["tunnel"]["profiles"] == ["tunnel"]
    assert services["admin"]["profiles"] == ["admin"]
    assert services["api"]["ports"][0].startswith("127.0.0.1:")
    assert config["networks"]["private"]["internal"] is True
    ingress = yaml.safe_load((ROOT / "local/tunnel.yml.example").read_text())["ingress"]
    assert [route["service"] for route in ingress] == ["http://api:8080", "http_status:404"]
    admissions = json.loads((ROOT / "core/sources/admission.json").read_text())
    assert not any(a["enabled"] for a in admissions["sources"])
    override = yaml.safe_load((ROOT / "local/compose.sources.yaml.example").read_text())
    assert override["services"]["scheduler"]["networks"] == ["private", "egress"]
    assert "SOURCE_ADMISSION_PATH" in override["services"]["scheduler"]["environment"]
