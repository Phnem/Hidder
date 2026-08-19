"""Small dependency-free CLI for the first Protocol Miner stage."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from miner.config import default_settings
from miner.orchestrator.ingest import ingest_file


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="miner.py", description="Peripheral Protocol Miner (static-only)")
    parser.add_argument("--cas-root", type=Path, help="Compatible content-addressed artifact store")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="Check core and optional tooling")
    ingest = commands.add_parser("ingest", help="Ingest a local artifact into CAS")
    ingest.add_argument("path", type=Path)
    ingest.add_argument("--vendor")
    return parser


def _doctor() -> int:
    print("CORE READY")
    print(f"Python {sys.version.split()[0]} available")
    for tool in ("node", "playwright", "frida", "analyzeHeadless", "7z", "asar"):
        print(f"{tool}: {'available' if shutil.which(tool) else 'unavailable'}")
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
    return 2
