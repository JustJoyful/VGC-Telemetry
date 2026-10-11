"""Host-side Compose operations with retained, consistent volume backups."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROJECTS = ("vgc-phase4a", "vgc-phase4a-restore", "vgc-phase5", "vgc-phase5-restore")
VOLUMES = ("dynamodb-data", "s3-data")


def command(*args, capture=False):
    if args[:2] == ("docker", "compose"):
        args = args[:2] + ("--file", str(ROOT / "compose.yaml")) + args[2:]
    return subprocess.run(
        args,
        cwd=ROOT,
        check=True,
        text=True,
        env=os.environ | {"COMPOSE_PROFILES": "", "COMPOSE_PROJECT_NAME": "vgc-phase4a"},
        stdout=subprocess.PIPE if capture else None,
    ).stdout


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", choices=PROJECTS, default=PROJECTS[0])
    parser.add_argument(
        "action",
        choices=["up", "down", "status", "seed-demo", "demo", "backup", "restore", "reset-demo"],
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--confirm-reset", action="store_true")
    args = parser.parse_args(argv)
    compose = ["docker", "compose", "-p", args.project]
    if not (ROOT / ".env.local").exists():
        shutil.copyfile(ROOT / "local/compose.env.example", ROOT / ".env.local")
    if args.action == "up":
        command(*compose, "up", "-d", "--build", "--wait", "--wait-timeout", "180")
    elif args.action == "down":
        command(*compose, "down")  # Named volumes retained; never pass -v.
    elif args.action == "status":
        command(*compose, "ps", "--all")
    elif args.action in {"seed-demo", "demo", "reset-demo"}:
        if args.action != "seed-demo" and not args.confirm_reset:
            parser.error("requires --confirm-reset (exact demo table/bucket only)")
        if args.output and args.output.exists():
            parser.error("output exists; choose a fresh evidence path")
        command(*compose, "stop", "worker", "scheduler")
        try:
            raw = command(
                *compose,
                "exec",
                "-T",
                "api",
                "python",
                "-m",
                "scripts.local.cli",
                args.action,
                *(["--confirm-reset"] if args.action != "seed-demo" else []),
                capture=True,
            )
            report = json.loads(raw.splitlines()[-1])
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                with args.output.open("x") as stream:
                    json.dump(report, stream, indent=2, sort_keys=True)
                    stream.write("\n")
            print(json.dumps(report, sort_keys=True))
        finally:
            command(*compose, "start", "worker", "scheduler")
    elif args.action == "backup":
        if args.output is None:
            parser.error("backup requires a fresh --output directory")
        destination = args.output.resolve()
        destination.mkdir(parents=True, exist_ok=False)
        command(*compose, "stop")
        try:
            hashes = {}
            for suffix in VOLUMES:
                command(
                    "docker",
                    "run",
                    "--rm",
                    "--network",
                    "none",
                    "--mount",
                    f"type=volume,src={args.project}_{suffix},dst=/data,readonly",
                    "--mount",
                    f"type=bind,src={destination},dst=/backup",
                    "alpine:3.22",
                    "tar",
                    "-cpf",
                    f"/backup/{suffix}.tar",
                    "-C",
                    "/data",
                    ".",
                )
                hashes[suffix] = hashlib.sha256(
                    (destination / f"{suffix}.tar").read_bytes()
                ).hexdigest()
            (destination / "manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "project": args.project,
                        "sha256": hashes,
                        "method": "all Compose services stopped; physical volume archives",
                    },
                    indent=2,
                )
                + "\n"
            )
        finally:
            command(*compose, "start")
        print(json.dumps({"backup": str(destination), "sha256": hashes}))
    else:
        if args.input is None:
            parser.error("restore requires --input backup directory")
        source = args.input.resolve()
        manifest = json.loads((source / "manifest.json").read_text())
        if manifest.get("schema_version") != 1 or set(manifest["sha256"]) != set(VOLUMES):
            raise ValueError("Invalid backup manifest")
        # Validate every archive before stopping or writing any target resource.
        for suffix in VOLUMES:
            archive = source / f"{suffix}.tar"
            if hashlib.sha256(archive.read_bytes()).hexdigest() != manifest["sha256"][suffix]:
                raise ValueError("Backup checksum mismatch")
            with tarfile.open(archive) as stream:
                for member in stream:
                    if (
                        member.name.startswith("/")
                        or ".." in Path(member.name).parts
                        or not (member.isfile() or member.isdir())
                    ):
                        raise ValueError("Unsafe archive member")
        command(*compose, "down")
        for suffix in VOLUMES:
            command("docker", "volume", "create", f"{args.project}_{suffix}")
            command(
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--mount",
                f"type=volume,src={args.project}_{suffix},dst=/data",
                "alpine:3.22",
                "sh",
                "-c",
                'test -z "$(ls -A /data)"',
            )
        for suffix in VOLUMES:
            command(
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--mount",
                f"type=volume,src={args.project}_{suffix},dst=/data",
                "--mount",
                f"type=bind,src={source},dst=/backup,readonly",
                "alpine:3.22",
                "tar",
                "-xpf",
                f"/backup/{suffix}.tar",
                "-C",
                "/data",
            )
        command(*compose, "up", "-d", "--wait", "--wait-timeout", "180")
        print(json.dumps({"restored": str(source), "project": args.project}))


if __name__ == "__main__":
    main()
