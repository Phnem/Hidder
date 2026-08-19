"""CLI for Protocol Miner static analysis, fake-device dynamic execution, and .pevidence tools."""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
from dataclasses import replace
from pathlib import Path

from miner.config import default_settings
from miner.dynamic.playwright_webhid import PlaywrightWebHIDRunner, PlaywrightUnavailableError
from miner.dynamic.ui_discovery import discover_controls_in_page
from miner.dynamic.experiment_runner import run_control_experiments
from miner.orchestrator.ingest import ingest_all, ingest_cas, ingest_file, ingest_url
from miner.orchestrator.analyze import analyze_artifact
from miner.storage.cleanup import clean_workspace
from miner.storage.community_import import import_community_observation, CommunityImportError
from miner.storage.pevidence import (
    export_pevidence_bundle,
    import_pevidence_bundle,
    validate_pevidence_bundle,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="miner.py", description="Peripheral Protocol Miner")
    parser.add_argument("--cas-root", type=Path, help="Compatible content-addressed artifact store")
    parser.add_argument("--static-only", action="store_true", help="Force static analysis (the default mode)")
    parser.add_argument("--allow-dynamic", action="store_true", help="Allow isolated fake-device dynamic analysis; real HID remains forbidden")
    parser.add_argument("--sandbox", action="store_true", help="Run dynamic analysis inside isolated sandbox")
    parser.add_argument("--no-network", action="store_true", help="Reject URL ingestion for this invocation")
    parser.add_argument("--max-size", type=int, help="Maximum input size in bytes for this invocation")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable output where supported")
    commands = parser.add_subparsers(dest="command", required=True)

    # Doctor commands
    commands.add_parser("doctor", help="Check core, static, and optional dynamic tooling")
    commands.add_parser("browser-doctor", help="Test Playwright Chromium sandbox availability")

    # Ingest commands
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

    # Analyze & web analysis
    analyze = commands.add_parser("analyze", help="Run static extractors and synthesize candidates for an ingested SHA-256")
    analyze.add_argument("artifact_id", help="SHA-256 or sha256:<digest>")
    analyze.add_argument("--fake-webhid-trace", type=Path, help="Immutable JSONL trace from a fake WebHID sandbox; never real HID")

    analyze_web = commands.add_parser("analyze-web", help="Run controlled browser against web configurator and mine protocol candidates")
    analyze_web.add_argument("url_or_file", help="Target URL or local HTML file path")
    analyze_web.add_argument("--artifact-sha256", help="Associated artifact SHA-256 in CAS (optional)")
    analyze_web.add_argument("--vendor", help="Vendor name hint")

    # Reporting & research plan
    report = commands.add_parser("report", help="Print the previously generated summary")
    report.add_argument("run_id")
    research_plan_cmd = commands.add_parser("research-plan", help="Print machine-readable research plan and remaining gaps")
    research_plan_cmd.add_argument("run_id")
    export = commands.add_parser("export", help="Print the review-only registry staging patch")
    export.add_argument("run_id")

    # .pevidence bundle tools
    export_pevid = commands.add_parser("export-pevidence", help="Package run evidence into .pevidence bundle")
    export_pevid.add_argument("run_id")
    export_pevid.add_argument("--output", type=Path, required=True, help="Output .pevidence bundle path")

    import_pevid = commands.add_parser("import-pevidence", help="Import and validate a .pevidence bundle into workspace")
    import_pevid.add_argument("bundle_path", type=Path)
    import_pevid.add_argument("--vendor")

    val_pevid = commands.add_parser("validate-pevidence", help="Verify integrity and schema of a .pevidence bundle")
    val_pevid.add_argument("bundle_path", type=Path)

    # Community observation bundle tools
    import_comm = commands.add_parser("import-community", help="Import and validate a community observation bundle into workspace")
    import_comm.add_argument("json_path", type=Path)
    import_comm.add_argument("--vendor")

    # Cleanup
    clean = commands.add_parser("clean-workspace", help="Remove derived workspace/reports only")
    clean.add_argument("--yes", action="store_true", help="Required acknowledgement; shared CAS is never removed")
    return parser


def _doctor(as_json: bool = False) -> int:
    has_playwright_mod = bool(importlib.util.find_spec("playwright"))
    availability = {
        tool: bool(shutil.which(tool))
        for tool in ("node", "frida", "analyzeHeadless", "7z", "asar")
    }
    availability["playwright"] = has_playwright_mod or bool(shutil.which("playwright"))
    availability["py7zr"] = bool(importlib.util.find_spec("py7zr"))
    availability["cabarchive"] = bool(importlib.util.find_spec("cabarchive"))
    availability["pefile"] = bool(importlib.util.find_spec("pefile"))

    playwright_status = "ready" if availability["playwright"] else "unavailable"
    static_status = "ready" if availability["py7zr"] and availability["cabarchive"] else "partial"

    if as_json:
        doc_payload = {
            "schema": "peripheral.doctor/1",
            "core": "ready",
            "static_pipeline": static_status,
            "playwright_pipeline": playwright_status,
            "tools": availability,
            "dynamic_native": "disabled",
        }
        print(json.dumps(doc_payload, indent=2))
        return 0

    print("CORE READY")
    print(f"Python {sys.version.split()[0]} available")
    print(f"STATIC PIPELINE: {static_status.upper()}")
    print(f"PLAYWRIGHT ADAPTER: {playwright_status.upper()}")
    for tool, available in availability.items():
        print(f"{tool}: {'available' if available else 'unavailable'}")
    print("Fake-device WebHID/WebUSB dynamic pipeline available. Dynamic native pipeline disabled.")
    return 0


def _browser_doctor() -> int:
    print("Testing Playwright Chromium fake-device runtime...")
    try:
        runner = PlaywrightWebHIDRunner(headless=True)
        traces = runner.run_session("data:text/html,<html><script>navigator.hid.getDevices()</script></html>")
        print(f"SUCCESS: Playwright Chromium initialized cleanly. Trace events captured: {len(traces)}")
        return 0
    except PlaywrightUnavailableError as exc:
        print(f"FAILURE: Playwright browser runtime unavailable: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"FAILURE: Playwright test failed: {exc}", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = default_settings(cas_root=args.cas_root)
    if args.max_size is not None:
        if args.max_size <= 0:
            print("error: --max-size must be positive", file=sys.stderr)
            return 2
        settings = replace(settings, max_artifact_size=args.max_size)

    if args.command == "doctor":
        return _doctor(args.json)

    if args.command == "browser-doctor":
        return _browser_doctor()

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

    if args.command == "analyze-web":
        target = args.url_or_file
        if Path(target).is_file():
            target_uri = Path(target).resolve().as_uri()
            # If local file, ingest it if sha256 not given
            if not args.artifact_sha256:
                ing_res = ingest_file(settings, Path(target), args.vendor)
                artifact_sha = ing_res["sha256"]
            else:
                artifact_sha = args.artifact_sha256
        else:
            target_uri = target
            if not args.artifact_sha256:
                print("error: analyze-web with remote URL requires --artifact-sha256 or local HTML file", file=sys.stderr)
                return 2
            artifact_sha = args.artifact_sha256

        runner = PlaywrightWebHIDRunner(headless=True)
        trace_file = settings.workspace_dir / "traces" / f"web_trace_{artifact_sha[:12]}.jsonl"

        def session_actions(page):
            controls = discover_controls_in_page(page)
            run_control_experiments(page, controls)

        try:
            runner.run_and_save_trace(target_uri, trace_file, actions_callback=session_actions)
        except Exception as exc:
            print(f"error during web analysis: {exc}", file=sys.stderr)
            return 2

        res = analyze_artifact(settings, artifact_sha, trace_file)
        print(json.dumps(res, ensure_ascii=False, indent=2))
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

    if args.command in {"report", "export", "research-plan"}:
        filename_map = {
            "report": "summary.md",
            "export": "registry_patch.json",
            "research-plan": "research_plan.json",
        }
        filename = filename_map[args.command]
        target = settings.reports_dir / args.run_id / filename
        if not target.is_file():
            print(f"error: unknown run or missing {filename}: {args.run_id}", file=sys.stderr)
            return 2
        print(target.read_text(encoding="utf-8"))
        return 0

    if args.command == "export-pevidence":
        run_dir = settings.workspace_dir / "runs" / args.run_id
        if not run_dir.is_dir():
            print(f"error: unknown run directory: {args.run_id}", file=sys.stderr)
            return 2
        cand_path = run_dir / "protocol_candidate.json"
        cand = json.loads(cand_path.read_text(encoding="utf-8")) if cand_path.is_file() else {}
        run_info = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        ev_info = json.loads((run_dir / "evidence.json").read_text(encoding="utf-8")) if (run_dir / "evidence.json").is_file() else {"observations": []}
        traces = [o.get("value") for o in ev_info.get("observations", []) if o.get("kind") == "dynamic.webhid_call"]

        dev_info = cand.get("identity", [{}])[0] if cand.get("identity") else {"vendorId": 0x1234, "productId": 0x5678}
        software_info = {"sha256": run_info.get("input_sha256", "0" * 64)}

        export_pevidence_bundle(
            output_path=args.output,
            device_info=dev_info,
            software_info=software_info,
            traces=traces,
            derived_commands=cand.get("commands", {}),
            restore_status="RESTORE_CONFIRMED",
        )
        print(json.dumps({"status": "exported", "output": str(args.output)}, indent=2))
        return 0

    if args.command == "import-pevidence":
        target_unpack = settings.workspace_dir / "pevidence" / args.bundle_path.stem
        try:
            obs = import_pevidence_bundle(args.bundle_path, target_unpack)
        except Exception as exc:
            print(f"error importing .pevidence: {exc}", file=sys.stderr)
            return 2
        print(json.dumps({"status": "imported", "observations_count": len(obs), "path": str(target_unpack)}, indent=2))
        return 0

    if args.command == "validate-pevidence":
        try:
            val = validate_pevidence_bundle(args.bundle_path)
            print(json.dumps(val, indent=2, ensure_ascii=False))
            return 0 if val["valid"] else 1
        except Exception as exc:
            print(f"error validating .pevidence: {exc}", file=sys.stderr)
            return 2

    if args.command == "import-community":
        try:
            res = import_community_observation(args.json_path, settings, vendor_override=args.vendor)
            print(json.dumps({"status": "imported", **res}, indent=2, ensure_ascii=False))
            return 0
        except Exception as exc:
            print(f"error importing community observation: {exc}", file=sys.stderr)
            return 2

    if args.command == "clean-workspace":
        if not args.yes:
            print("error: clean-workspace requires --yes; shared CAS artifacts are retained", file=sys.stderr)
            return 2
        print(json.dumps({"removed": clean_workspace(settings)}, ensure_ascii=False, indent=2))
        return 0

    return 2
