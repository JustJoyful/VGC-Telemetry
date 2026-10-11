"""Record host/container/input provenance, run a bounded benchmark, retain new evidence."""

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

from scripts.benchmarks.local import BenchmarkReport, source_identity, verify_identity

ROOT = Path(__file__).resolve().parents[2]


def output(*args):
    return subprocess.check_output(args, cwd=ROOT, text=True).strip()


def main():
    os.environ.update(
        COMPOSE_FILE=str(ROOT / "compose.yaml"),
        COMPOSE_PROJECT_NAME="vgc-phase4a",
        COMPOSE_PROFILES="",
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--confirm-reset", action="store_true")
    args = parser.parse_args()
    command_started = time.perf_counter()
    if not args.confirm_reset or args.output.exists():
        parser.error("requires --confirm-reset and a new --output path")
    tree_hash, inputs = source_identity(ROOT)
    ids = output("docker", "compose", "ps", "-q").splitlines()
    containers = json.loads(output("docker", "inspect", *ids))
    metadata = {
        "revision": output("git", "rev-parse", "HEAD"),
        "source_tree_sha256": tree_hash,
        "inputs": inputs,
        "command": ["uv", "run", "--frozen", "python", "-m", "scripts.benchmarks.run_local"]
        + sys.argv[1:],
        "environment": {
            "host_os": platform.platform(),
            "host_architecture": platform.machine(),
            "host_cpu": next(
                (
                    line.split(":", 1)[1].strip()
                    for line in Path("/proc/cpuinfo").read_text().splitlines()
                    if line.startswith("model name")
                ),
                "unknown",
            ),
            "host_ram": Path("/proc/meminfo").read_text().splitlines()[0],
            "host_cpu_count": __import__("os").cpu_count(),
            "uv": output("uv", "--version"),
            "docker": output("docker", "version", "--format", "{{.Server.Version}}"),
            "compose": output("docker", "compose", "version", "--short"),
            "uv_lock_sha256": hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest(),
            "containers": [
                {
                    "name": c["Name"],
                    "image_id": c["Image"],
                    "memory_limit_bytes": c["HostConfig"]["Memory"],
                    "nano_cpus": c["HostConfig"]["NanoCpus"],
                }
                for c in containers
            ],
        },
    }
    api_identity = json.loads(
        output(
            "docker",
            "compose",
            "exec",
            "-T",
            "api",
            "python",
            "-c",
            "import json; from pathlib import Path; "
            "from scripts.benchmarks.local import source_identity; "
            "print(json.dumps(source_identity(Path('/work'))))",
        )
    )
    verify_identity(metadata, tuple(api_identity))
    metadata["environment"]["verified_api_source_tree_sha256"] = api_identity[0]
    evidence = ROOT / ".local/benchmark-provenance"
    evidence.mkdir(parents=True, exist_ok=True)
    provenance_path = evidence / (tree_hash + ".json")
    provenance_path.write_text(json.dumps(metadata, indent=2) + "\n")
    subprocess.run(["docker", "compose", "stop", "worker", "scheduler"], cwd=ROOT, check=True)
    container_name = "vgc-phase4a-benchmark-" + uuid4().hex
    try:
        raw = output(
            "docker",
            "compose",
            "run",
            "--name",
            container_name,
            "--no-deps",
            "-v",
            f"{provenance_path}:/tmp/provenance.json:ro",
            "api",
            "python",
            "-m",
            "scripts.benchmarks.local",
            "--provenance",
            "/tmp/provenance.json",
            "--repeats",
            str(args.repeats),
            "--confirm-reset",
        )
        report = BenchmarkReport.model_validate_json(raw.splitlines()[-1])
        report.environment["benchmark_image_id"] = output(
            "docker", "inspect", "--format", "{{.Image}}", container_name
        )
        report.timings["host_command_wall_seconds"] = time.perf_counter() - command_started
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x") as stream:
            stream.write(report.model_dump_json(indent=2) + "\n")
        print(
            json.dumps(
                {
                    "output": str(args.output),
                    "revision": report.revision,
                    "tree_sha256": report.source_tree_sha256,
                    "wall_seconds": report.timings["driver_wall_seconds"],
                    "items": report.dataset["persisted_items"],
                }
            )
        )
    finally:
        subprocess.run(
            ["docker", "rm", container_name],
            cwd=ROOT,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(["docker", "compose", "start", "worker", "scheduler"], cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
