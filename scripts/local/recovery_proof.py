"""Synthetic public-write interruption followed by actual worker startup recovery."""

import argparse
import json
import os
import subprocess
import time
from pathlib import Path

from scripts.local.stack import ROOT, command

READ = (
    "from db.client import get_dynamodb_table; from scripts.local.demo import pointer,summary; "
    "import json; t=get_dynamodb_table(); "
    "print(json.dumps({'snapshot_id':pointer(t),'summary':summary(t)}))"
)


def state():
    return json.loads(
        command(
            "docker", "compose", "exec", "-T", "api", "python", "-c", READ, capture=True
        ).splitlines()[-1]
    )


def main():
    os.environ.update(
        COMPOSE_FILE=str(ROOT / "compose.yaml"),
        COMPOSE_PROJECT_NAME="vgc-phase4a",
        COMPOSE_PROFILES="",
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("choose a new evidence output")
    command("docker", "compose", "stop", "worker", "scheduler")
    try:
        before = state()
        command(
            "docker",
            "compose",
            "exec",
            "-T",
            "api",
            "python",
            "-m",
            "ingestion.public_replay",
            "invalidate",
            "--policy-version",
            "limitless-single-bracket-v1",
        )
        failure = subprocess.run(
            [
                "docker",
                "compose",
                "exec",
                "-T",
                "api",
                "python",
                "-m",
                "scripts.local.cli",
                "publish",
                "--synthetic-fault",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        assert failure.returncode != 0
        assert "RuntimeError: synthetic-after-first-public-team-write" in failure.stderr
        interrupted = state()
        assert interrupted == before
        # Termination/restart discards process memory; recovery uses only durable DB/S3 state.
        command("docker", "compose", "restart", "worker")
        started = time.monotonic()
        observations = 0
        while time.monotonic() - started < 900:
            current = state()
            observations += 1
            assert current["summary"] == before["summary"]
            if current["snapshot_id"] != before["snapshot_id"]:
                break
            time.sleep(5)
        else:
            raise RuntimeError("Worker startup recovery exceeded 900 seconds")
        command(
            "docker",
            "compose",
            "exec",
            "-T",
            "api",
            "python",
            "-m",
            "scripts.local.cli",
            "api-proof",
        )
        report = {
            "fault": "synthetic-after-first-public-team-write",
            "before": before,
            "after_fault": interrupted,
            "after_restart": current,
            "complete_pointer_observations": observations,
            "recovery_wall_seconds": time.monotonic() - started,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x") as stream:
            json.dump(report, stream, indent=2)
            stream.write("\n")
        print(json.dumps(report))
    finally:
        command("docker", "compose", "start", "worker", "scheduler")


if __name__ == "__main__":
    main()
