"""Host-side Compose operations with retained, consistent volume backups."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROJECTS = ("vgc-phase4a", "vgc-phase4a-restore", "vgc-phase5", "vgc-phase5-restore")
VOLUMES = ("dynamodb-data", "s3-data")
ROLES = ("worker", "scheduler")
LIVE_FILES = ("compose.yaml", "compose.live.yaml")
LIVE_ADMISSION = ROOT / ".local/admission.json"


def command(*args, capture=False, files=("compose.yaml",)):
    if args[:2] == ("docker", "compose"):
        selected = tuple(part for name in files for part in ("--file", str(ROOT / name)))
        if (ROOT / ".env.local").exists():
            # Interpolation (e.g. VGC_API_PORT) reads .env.local, not only the shell.
            selected += ("--env-file", str(ROOT / ".env.local"))
        args = args[:2] + selected + args[2:]
    return subprocess.run(
        args,
        cwd=ROOT,
        check=True,
        text=True,
        env=os.environ | {"COMPOSE_PROFILES": "", "COMPOSE_PROJECT_NAME": "vgc-phase4a"},
        stdout=subprocess.PIPE if capture else None,
    ).stdout


def ensure_live_admission():
    """Create this installation's explicit live admission from the bundled reviewed entry."""
    if LIVE_ADMISSION.is_dir():
        raise SystemExit(
            f"{LIVE_ADMISSION} is a directory (Docker creates one when the file is missing). "
            "Remove that empty directory and run the live command again."
        )
    if LIVE_ADMISSION.exists():
        return False
    bundled = json.loads((ROOT / "core/sources/admission.json").read_text())
    for source in bundled["sources"]:
        # Only the evidence-backed, verified entry can be enabled; coverage is unchanged.
        source["enabled"] = bool(source.get("verified") and source.get("evidence"))
    LIVE_ADMISSION.parent.mkdir(exist_ok=True)
    LIVE_ADMISSION.write_text(json.dumps(bundled, indent=2) + "\n")
    print(
        f"Created {LIVE_ADMISSION.relative_to(ROOT)} enabling the bundled Limitless admission "
        'for this installation only. Set "enabled": false there to stop live collection.',
        file=sys.stderr,
    )
    return True


def api_port():
    for line in (ROOT / ".env.local").read_text().splitlines():
        if line.startswith("VGC_API_PORT="):
            return line.split("=", 1)[1].strip()
    return os.environ.get("VGC_API_PORT", "8080")


def run_collector(compose, events=None):
    """Run the foreground refresh; returns its JSON report even when it exits non-zero."""
    extra = ["-e", f"LIVE_REFRESH_EVENTS={events}"] if events else []
    try:
        raw = command(
            *compose, "run", "--rm", "-T", *extra, "collector", capture=True, files=LIVE_FILES
        )
    except subprocess.CalledProcessError as error:
        raw = error.output or ""
    for line in reversed(raw.splitlines()):
        if line.startswith("{"):
            return json.loads(line)
    return {
        "status": "failed",
        "reason": "collector_error",
        "message": "The collector exited without a report; see the log output above.",
    }


def summarize(report):
    print(json.dumps(report, sort_keys=True), flush=True)
    lines = [f"Live refresh: {report['status']} - {report.get('message', '')}"]
    for source, stats in (report.get("sources") or {}).items():
        lines.append(
            f"  {source}: discovered {stats['discovered']}, captured {stats['captured']}, "
            f"published-eligible {stats['accepted']}, pending {stats['pending']}, "
            f"rejected {stats['rejected']}, failed {stats['failed']}, "
            f"queued for background {stats['remaining']}"
        )
        if stats.get("diagnostics"):
            lines.append(f"  diagnostics: {json.dumps(stats['diagnostics'], sort_keys=True)}")
    if report.get("reason"):
        lines.append(f"  reason: {report['reason']}")
    lines.append(f"  Explorer: http://127.0.0.1:{api_port()}/explorer")
    print("\n".join(lines), file=sys.stderr, flush=True)


def live_refresh(compose, events=None):
    """Pause background roles, refresh in the foreground, then restore prior service state."""
    running = set(
        command(
            *compose, "ps", "--status", "running", "--services", capture=True, files=LIVE_FILES
        ).split()
    )
    command(*compose, "stop", *ROLES, files=LIVE_FILES)
    try:
        report = run_collector(compose, events)
        # Report the collection outcome before restoring roles, so it is never lost.
        summarize(report)
    finally:
        paused = [role for role in ROLES if role in running]
        if paused:
            command(*compose, "start", *paused, files=LIVE_FILES)
        # `run` starts storage dependencies; leave a previously stopped stack stopped.
        started = sorted({"dynamodb", "storage"} - running)
        if started and not paused and "api" not in running:
            command(*compose, "stop", *started, files=LIVE_FILES)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", choices=PROJECTS, default=PROJECTS[0])
    parser.add_argument(
        "action",
        choices=[
            "up",
            "live",
            "refresh",
            "down",
            "status",
            "seed-demo",
            "demo",
            "backup",
            "restore",
            "reset-demo",
        ],
    )
    parser.add_argument(
        "--events", type=int, help="live/refresh: newest events to capture now (1-100)"
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--confirm-reset", action="store_true")
    args = parser.parse_args(argv)
    compose = ["docker", "compose", "-p", args.project]
    if not (ROOT / ".env.local").exists():
        shutil.copyfile(ROOT / "local/compose.env.example", ROOT / ".env.local")
    if args.events is not None and not 1 <= args.events <= 100:
        parser.error("--events must be 1-100")
    if args.action == "up":
        command(*compose, "up", "-d", "--build", "--wait", "--wait-timeout", "180")
    elif args.action in {"live", "refresh"}:
        ensure_live_admission()
        if args.action == "live":
            # Start storage and the API only; scheduled collection waits for the refresh.
            command(
                *compose,
                "up",
                "-d",
                "--build",
                "--wait",
                "--wait-timeout",
                "180",
                "api",
                files=LIVE_FILES,
            )
            command(*compose, "stop", *ROLES, files=LIVE_FILES)
            try:
                report = run_collector(compose, args.events)
                summarize(report)
            finally:
                # Recreate roles with the live overlay after the foreground refresh finishes.
                command(
                    *compose,
                    "up",
                    "-d",
                    "--wait",
                    "--wait-timeout",
                    "180",
                    *ROLES,
                    files=LIVE_FILES,
                )
        else:
            report = live_refresh(compose, args.events)
        if report["status"] in {"failed", "disabled"}:
            raise SystemExit(1)
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
