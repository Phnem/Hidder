"""Small dependency-free CLI for the first Protocol Miner stage."""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
from dataclasses import replace
from pathlib import Path

from miner.config import default_settings
from miner.orchestrator.ingest import ingest_all, ingest_cas, ingest_file, ingest_url
from miner.orchestrator.analyze import analyze_artifact
from miner.storage.cleanup import clean_workspace


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="miner.py", description="Peripheral Protocol Miner (static-only)")
    parser.add_argument("--cas-root", type=Path, help="Compatible content-addressed artifact store")
    parser.add_argument("--static-only", action="store_true", help="Force static analysis (the default and only active mode)")
    parser.add_argument("--allow-dynamic", action="store_true", help="Reserved for future isolated adapters; real HID remains forbidden")
    parser.add_argument("--sandbox", action="store_true", help="Reserved for future fake-device sandbox adapters")
    parser.add_argument("--no-network", action="store_true", help="Reject URL ingestion for this invocation")
    parser.add_argument("--max-size", type=int, help="Maximum input size in bytes for this invocation")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="Check core and optional tooling")
    ingest = commands.add_parser("ingest", help="Ingest a local artifact into CAS")
    ingest.add_argument("path", type=Path)
    ingest.add_argument("--vendor")
    ingest_url = commands.add_parser("ingest-url", help="Download and ingest an artifact without execution")
    ingest_url.add_argument("url")
    ingest_url.add_argument("--vendor")
    ingest_all_parser = commands.add_parser("ingest-all", help="Ingest every file in inbox/")
    ingest_all_parser.add_argument("--vendor")
    ingest_cas_parser = commands.add_parser("ingest-cas", help="Register an existing compatible CAS SHA-256")
    ingest_cas_parser.add_argument("sha256")
    ingest_cas_parser.add_argument("--filename", required=True, help="Original filename for type detection and provenance")
    ingest_cas_parser.add_argument("--vendor")
    analyze = commands.add_parser("analyze", help="Run static extractors for an ingested SHA-256")
    analyze.add_argument("artifact_id", help="SHA-256 or sha256:<digest>")
    analyze.add_argument("--fake-webhid-trace", type=Path, help="Immutable JSONL trace from a fake WebHID sandbox; never real HID")
    report = commands.add_parser("report", help="Print the previously generated summary")
    report.add_argument("run_id")
    export = commands.add_parser("export", help="Print the review-only registry staging patch")
    export.add_argument("run_id")
    clean = commands.add_parser("clean-workspace", help="Remove derived workspace/reports only")
    clean.add_argument("--yes", action="store_true", help="Required acknowledgement; shared CAS is never removed")
    return parser


def _doctor() -> int:
    print("CORE READY")
    print(f"Python {sys.version.split()[0]} available")
    for tool in ("node", "playwright", "frida", "analyzeHeadless", "7z", "asar"):
        print(f"{tool}: {'available' if shutil.which(tool) else 'unavailable'}")
    print(f"py7zr: {'available' if importlib.util.find_spec('py7zr') else 'unavailable'}")
    print("Static pipeline available. Dynamic native pipeline disabled.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = default_settings(cas_root=args.cas_root)
    if args.max_size is not None:
        if args.max_size <= 0:
            print("error: --max-size must be positive", file=sys.stderr)
            return 2
        settings = replace(settings, max_artifact_size=args.max_size)
    if (args.allow_dynamic and not getattr(args, "fake_webhid_trace", None)) or args.sandbox:
        print("note: dynamic adapters are unavailable; continuing in static-only mode", file=sys.stderr)
    if args.command == "doctor":
        return _doctor()
    if args.command == "ingest":
        try:
            result = ingest_file(settings, args.path, args.vendor)
        except (OSError, ValueError) as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "analyze":
        try:
            if args.fake_webhid_trace and not args.allow_dynamic:
                print("error: --fake-webhid-trace requires explicit --allow-dynamic", file=sys.stderr)
                return 2
            result = analyze_artifact(settings, args.artifact_id, args.fake_webhid_trace)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "ingest-url":
        if args.no_network:
            print("error: --no-network rejects ingest-url", file=sys.stderr)
            return 2
        try:
            result = ingest_url(settings, args.url, args.vendor)
        except (OSError, ValueError) as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "ingest-all":
        try:
            result = ingest_all(settings, args.vendor)
        except (OSError, ValueError) as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "ingest-cas":
        try:
            result = ingest_cas(settings, args.sha256, args.filename, args.vendor)
        except (OSError, ValueError) as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command in {"report", "export"}:
        filename = "summary.md" if args.command == "report" else "registry_patch.json"
        target = settings.reports_dir / args.run_id / filename
        if not target.is_file():
            print(f"error: unknown run or missing {filename}: {args.run_id}", file=sys.stderr)
            return 2
        print(target.read_text(encoding="utf-8"))
        return 0
    if args.command == "clean-workspace":
        if not args.yes:
            print("error: clean-workspace requires --yes; shared CAS artifacts are retained", file=sys.stderr)
            return 2
        print(json.dumps({"removed": clean_workspace(settings)}, ensure_ascii=False, indent=2))
        return 0
    return 2
