"""Small dependency-free CLI for the first Protocol Miner stage."""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
from pathlib import Path

from miner.config import default_settings
from miner.orchestrator.ingest import ingest_all, ingest_file, ingest_url
from miner.orchestrator.analyze import analyze_artifact
from miner.storage.cleanup import clean_workspace


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="miner.py", description="Peripheral Protocol Miner (static-only)")
    parser.add_argument("--cas-root", type=Path, help="Compatible content-addressed artifact store")
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
    analyze = commands.add_parser("analyze", help="Run static extractors for an ingested SHA-256")
    analyze.add_argument("artifact_id", help="SHA-256 or sha256:<digest>")
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
    print("Static foundation available. Dynamic native pipeline disabled.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "doctor":
        return _doctor()
    if args.command == "ingest":
        try:
            result = ingest_file(default_settings(cas_root=args.cas_root), args.path, args.vendor)
        except (OSError, ValueError) as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "analyze":
        try:
            result = analyze_artifact(default_settings(cas_root=args.cas_root), args.artifact_id)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "ingest-url":
        try:
            result = ingest_url(default_settings(cas_root=args.cas_root), args.url, args.vendor)
        except (OSError, ValueError) as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "ingest-all":
        try:
            result = ingest_all(default_settings(cas_root=args.cas_root), args.vendor)
        except (OSError, ValueError) as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command in {"report", "export"}:
        settings = default_settings(cas_root=args.cas_root)
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
        print(json.dumps({"removed": clean_workspace(default_settings(cas_root=args.cas_root))}, ensure_ascii=False, indent=2))
        return 0
    return 2
