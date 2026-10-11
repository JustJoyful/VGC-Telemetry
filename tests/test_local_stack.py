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


def test_readme_reports_verified_local_acceptance():
    from scripts.local.stack import ROOT

    assert "self-hosted and verified" in (ROOT / "README.md").read_text()
