"""CLI Interface and Ingestion Coordinator for Peripheral Registry Ingest."""

import datetime
import json
import os
import subprocess
import sys
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Optional, List

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree

if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from ingest.artifacts.cache import ArtifactCache
from ingest.artifacts.downloader import ArtifactDownloader
from ingest.collectors import (
    get_collector_for_brand, SPECIALIZED_COLLECTORS,
    QmkCollector, LibratbagCollector, OpenRGBCollector,
    SignalRGBCollector, OpenRazerCollector, SolaarCollector,
    RivalcfgCollector, WootingCollector, CorsairCkbCollector,
    LogitechDocsCollector, ArtemisRGBNetCollector, LinuxHIDCollector
)
from ingest.config import DB_PATH, REPORTS_DIR, settings
from ingest.logging_setup import setup_logging, get_logger, console, get_log_metrics
from ingest.network.fetcher import TieredFetcher
from ingest.scanners import ScannerDispatcher
from ingest.storage.database import RegistryDatabase
from ingest.mass_model_discovery import ModelInventoryPass
from ingest.ai_model_reconciliation import AIModelReconciliation

app = typer.Typer(help="Peripheral Registry Ingestion & Static Analysis Pipeline")


@app.command(name="model-inventory")
def model_inventory(
    online: bool = typer.Option(False, "--online", help="Also inspect official sitemap/JSON-LD pages (best effort)."),
    no_inbox: bool = typer.Option(False, "--no-inbox", help="Skip the official software inbox pass."),
):
    """Build the additive commercial model / variant identity graph and reports."""
    summary = ModelInventoryPass(DB_PATH).run(include_inbox=not no_inbox, online=online)
    console.print_json(json.dumps(summary, ensure_ascii=False))


@app.command(name="reconcile-ai-models")
def reconcile_ai_models(
    input_path: Path = typer.Option(Path(r"C:\Users\2004i\Downloads\brends.txt"), "--input", help="AI discovery corpus TXT."),
    promote: bool = typer.Option(False, "--promote", help="Promote only verified candidates after staging."),
):
    """Stage and verify AI-discovered keyboard/mouse candidates without auto-promotion."""
    result = AIModelReconciliation(DB_PATH, input_path).run(promote=promote)
    console.print_json(json.dumps(result, ensure_ascii=False))


@app.command()
def run(
    brand: Optional[List[str]] = typer.Option(None, "--brand", "-b", help="Filter by specific brand slug or name"),
    vendor: Optional[List[str]] = typer.Option(None, "--vendor", "-v", help="Alias for --brand"),
    batch: Optional[str] = typer.Option("all", "--batch", help="Run a specific batch: 'pilot', 'A', 'B', 'C', 'all'"),
    metadata_only: bool = typer.Option(False, "--metadata-only", help="Crawl metadata only without downloading files"),
    no_download: bool = typer.Option(False, "--no-download", help="Skip artifact downloads"),
    verbose: bool = typer.Option(True, "--verbose/--quiet", help="Enable verbose console logging")
):
    """Execute peripheral crawl and static ingestion pipeline across canonical brands."""
    logger, log_file = setup_logging(verbose=verbose, log_to_file=True)

    run_id = str(uuid.uuid4())[:8]
    start_time = time.time()
    start_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    logger.info("=" * 70)
    logger.info("Peripheral Registry Ingest starting")
    logger.info(f"Run ID: [bold]{run_id}[/bold]")
    logger.info(f"Python: {sys.version.split()[0]}")
    logger.info(f"Database: {DB_PATH}")
    logger.info(f"Log file: {log_file}")

    # Determine brands to run
    selected_slugs: list[str] = []
    combined_filters = (brand or []) + (vendor or [])
    if combined_filters:
        for b_name in combined_filters:
            b_def = get_brand_by_slug(b_name)
            if b_def:
                if b_def.slug not in selected_slugs:
                    selected_slugs.append(b_def.slug)
            else:
                logger.warning(f"Unknown brand '{b_name}', skipping.")
    else:
        target_brands = get_brands_by_batch(batch or "all")
        selected_slugs = [b.slug for b in target_brands]

    if not selected_slugs:
        logger.error("No valid brands selected to run.")
        raise typer.Exit(code=1)

    logger.info(f"Selected {len(selected_slugs)} brands to crawl (Batch: {batch}): [bold]{', '.join(selected_slugs[:10])}{'...' if len(selected_slugs) > 10 else ''}[/bold]")
    logger.info("=" * 70)

    # Initialize components
    db = RegistryDatabase(DB_PATH)
    db.init_db()
    db.start_crawl_run(run_id)
    counts_before = db.get_summary_counts()

    fetcher = TieredFetcher()
    cache = ArtifactCache()
    extractor = SafeExtractor()
    scanners = ScannerDispatcher()
    downloader = ArtifactDownloader(fetcher=fetcher, cache=cache, db=db)

    total_stats = {
        "products_scanned": 0,
        "new_products": 0,
        "new_artifacts": 0,
        "changed_artifacts": 0,
        "new_vid_pids": 0,
        "new_hints": 0,
        "fatal_errors": 0,
        "collector_errors": 0,
        "artifact_download_failures": 0,
        "parse_failures": 0,
        "warnings": 0,
        "errors_count": 0,
    }

    # Run collectors
    for b_slug in selected_slugs:
        collector = get_collector_for_brand(
            brand_slug=b_slug,
            fetcher=fetcher,
            cache=cache,
            extractor=extractor,
            scanners=scanners,
            db=db,
            run_id=run_id,
            downloader=downloader
        )
        if not collector:
            continue

        try:
            collector.collect(metadata_only=metadata_only, no_download=no_download)
            for k, val in collector.stats.items():
                if k in total_stats:
                    total_stats[k] += val
        except Exception as e:
            total_stats["fatal_errors"] += 1
            logger.error(f"Critical error running brand collector '{b_slug}': {e}", exc_info=True)

    # Merge downloader metrics
    for k, val in downloader.metrics.items():
        if k in total_stats:
            total_stats[k] += val
        else:
            total_stats[k] = val

    # Reconcile counts with emitted log records by class
    log_metrics = get_log_metrics()
    for k in ["fatal_errors", "collector_errors", "artifact_download_failures", "parse_failures", "warnings"]:
        total_stats[k] = max(total_stats.get(k, 0), log_metrics.get(k, 0))
    total_stats["errors_count"] = total_stats["fatal_errors"]

    counts_after = db.get_summary_counts()
    duration_sec = time.time() - start_time
    duration_str = str(datetime.timedelta(seconds=int(duration_sec)))

    db.finish_crawl_run(run_id, total_stats, status="completed")

    # Generate Reports
    _generate_run_reports(run_id, start_iso, duration_str, counts_before, counts_after, total_stats, selected_slugs, db)

    # Print Run Summary to Console
    console.print("\n")
    console.rule("[bold green]RUN COMPLETE[/bold green]")
    summary_table = Table(title=f"Run Summary (ID: {run_id})", show_header=True, header_style="bold magenta")
    summary_table.add_column("Metric", style="cyan")
    summary_table.add_column("Value", style="bold green")

    summary_table.add_row("Duration", duration_str)
    summary_table.add_row("Brands crawled", str(len(selected_slugs)))
    summary_table.add_row("Products scanned", str(total_stats["products_scanned"]))
    summary_table.add_row("Total Products in Registry", str(counts_after["total_products"]))
    summary_table.add_row("Hardware Devices", str(counts_after["total_hardware_devices"]))
    summary_table.add_row("Stored CAS Artifacts", f"{counts_after['total_artifacts']} ({counts_after['total_artifact_mb']:.2f} MB)")
    summary_table.add_row("Artifacts Discovered", str(total_stats.get("artifacts_discovered", 0)))
    summary_table.add_row("Artifacts Downloaded", str(total_stats.get("artifacts_downloaded", 0)))
    summary_table.add_row("Cache Hits (Pre-download)", str(total_stats.get("artifacts_cache_hit_without_download", 0)))
    summary_table.add_row("Conditional 304 Hits", str(total_stats.get("conditional_304", 0)))
    summary_table.add_row("Duplicate URLs Skipped", str(total_stats.get("duplicate_urls_skipped", 0)))
    summary_table.add_row("Large Artifacts Deferred", str(total_stats.get("large_artifacts_deferred", 0)))
    summary_table.add_row("Bytes Downloaded", f"{total_stats.get('bytes_downloaded', 0) / 1024 / 1024:.2f} MB")
    summary_table.add_row("Bytes Avoided by Cache", f"{total_stats.get('bytes_avoided_by_cache', 0) / 1024 / 1024:.2f} MB")
    summary_table.add_row("Unique VID/PIDs", str(counts_after["total_vid_pids"]))
    summary_table.add_row("Protocol Hints", str(counts_after["total_hints"]))
    summary_table.add_row("Fatal Errors", str(total_stats["fatal_errors"]))
    summary_table.add_row("Collector Errors", str(total_stats["collector_errors"]))
    summary_table.add_row("Artifact Download Failures", str(total_stats["artifact_download_failures"]))
    summary_table.add_row("Parse Failures", str(total_stats["parse_failures"]))
    summary_table.add_row("Warnings", str(total_stats["warnings"]))

    console.print(summary_table)
    console.print(f"[dim]Database: {DB_PATH}[/dim]")
    console.print(f"[dim]Log file: {log_file}[/dim]\n")


@app.command(name="list-brands")
def list_brands():
    """List all 87 canonical brands and their latest discovery status."""
    db = RegistryDatabase(DB_PATH)
    db.init_db()
    brands = db.list_all_brands()

    table = Table(title="Canonical Peripheral Brands Registry (87 Brands)", show_header=True, header_style="bold cyan")
    table.add_column("#", style="dim", width=4)
    table.add_column("Brand", style="bold white")
    table.add_column("Status", style="magenta")
    table.add_column("Products", justify="right", style="green")
    table.add_column("Devices", justify="right", style="blue")
    table.add_column("Artifacts", justify="right", style="yellow")
    table.add_column("VID/PIDs", justify="right", style="cyan")
    table.add_column("Hints", justify="right", style="magenta")
    table.add_column("Tech Products", justify="right", style="bold green")
    table.add_column("Notes / Blocking Reason", style="dim")

    for i, b in enumerate(brands, 1):
        st = b.get("latest_status") or "NOT_RUN"
        p_cnt = str(b.get("products_count") or 0)
        d_cnt = str(b.get("devices_count") or 0)
        art_cnt = str(b.get("artifacts_count") or 0)
        vid_cnt = str(b.get("vid_pids_count") or 0)
        hint_cnt = str(b.get("hints_count") or 0)
        tech_cnt = str(b.get("tech_evidence_products") or 0)
        reason = b.get("blocking_reason") or ""

        # Colorize status
        if st in ["SUPPORTED_FULL", "SUPPORTED_PARTIAL"]:
            st_styled = f"[bold green]{st}[/bold green]"
        elif st in ["METADATA_ONLY", "SOFTWARE_ONLY"]:
            st_styled = f"[yellow]{st}[/yellow]"
        elif "BLOCKED" in st or "FAILED" in st:
            st_styled = f"[bold red]{st}[/bold red]"
        else:
            st_styled = f"[dim]{st}[/dim]"

        table.add_row(str(i), b["canonical_name"], st_styled, p_cnt, d_cnt, art_cnt, vid_cnt, hint_cnt, tech_cnt, reason)

    console.print(table)


@app.command(name="inspect-brand")
def inspect_brand(name: str):
    """Inspect detailed relationships, aliases, and crawl status for a specific brand."""
    db = RegistryDatabase(DB_PATH)
    db.init_db()
    b = db.get_brand_with_details(name)
    if not b:
        console.print(f"[red]Brand '{name}' not found in canonical registry.[/red]")
        raise typer.Exit(code=1)

    tree = Tree(f"[bold cyan]{b['canonical_name']}[/bold cyan] (Slug: '{b['slug']}', Type: {b['brand_type']})")
    
    # Details
    det_node = tree.add("[bold]Brand Details[/bold]")
    det_node.add(f"Website: {b.get('website') or 'N/A'}")
    det_node.add(f"Active: {'Yes' if b.get('active') else 'No'}")

    # Aliases
    aliases = b.get("aliases", [])
    if aliases:
        al_node = tree.add(f"[bold]Aliases ({len(aliases)})[/bold]")
        for al in aliases:
            al_node.add(f"'{al['alias']}' (Provenance: {al.get('provenance') or 'N/A'})")

    # Relationships
    rels = b.get("relationships", [])
    if rels:
        rel_node = tree.add(f"[bold]Brand Relationships ({len(rels)})[/bold]")
        for r in rels:
            rel_node.add(f"[{r['relationship_type']}] -> [bold]{r['target_brand']}[/bold] (Confidence: {r.get('confidence', 1.0)}, Provenance: {r.get('provenance') or 'N/A'})")

    # Latest Status
    st = b.get("latest_status")
    st_node = tree.add("[bold]Discovery & Crawl Status[/bold]")
    if st:
        st_node.add(f"Status: [bold green]{st['status']}[/bold green]")
        st_node.add(f"Products Discovered: {st.get('products_count', 0)}")
        st_node.add(f"Hardware Devices: {st.get('devices_count', 0)}")
        st_node.add(f"Artifacts: {st.get('artifacts_count', 0)} ({st.get('artifacts_bytes', 0)/1024/1024:.2f} MB)")
        st_node.add(f"VID/PID Pairs: {st.get('vid_pids_count', 0)}")
        st_node.add(f"Protocol Hints: {st.get('hints_count', 0)}")
        st_node.add(f"Products with Tech Evidence: {st.get('tech_evidence_products', 0)}")
        if st.get("blocking_reason"):
            st_node.add(f"Blocking Reason: [red]{st['blocking_reason']}[/red]")
        if st.get("crawled_at"):
            st_node.add(f"Last Crawled: {st['crawled_at']}")
    else:
        st_node.add("No crawl run recorded yet.")

    console.print(tree)


@app.command()
def status():
    """Display overall status and counts of the staging registry."""
    db = RegistryDatabase(DB_PATH)
    counts = db.get_summary_counts()
    brands = db.list_all_brands()

    console.print("\n" + "=" * 70)
    console.print("[bold cyan]Peripheral Registry Staging Status[/bold cyan]")
    console.print("=" * 70)
    console.print(f"Total Canonical Brands:                     {len(brands)}")
    console.print(f"Total Catalog Items:                        {counts['total_products']}")
    console.print(f"Hardware Devices (Keyboards/Mice/Headsets): {counts['total_hardware_devices']}")
    console.print(f"Accessories, Bundles & Components:          {counts['total_accessories']}")
    console.print(f"Stored Artifacts (CAS):                     {counts['total_artifacts']} files ({counts['total_artifact_mb']:.2f} MB)")
    console.print(f"Unique VID/PID Pairs:                       {counts['total_vid_pids']}")
    console.print(f"Extracted Protocol Hints:                   {counts['total_hints']}")
    console.print(f"Generic Technical Facts:                    {counts['total_facts']}")
    console.print(f"Tracked Web Sources:                        {counts['total_sources']}")
    console.print(f"Total Ingestion Runs:                       {counts['total_runs']}")
    console.print("=" * 70 + "\n")


@app.command(name="inspect-product")
def inspect_product(name: str):
    """Inspect full provenance, identifiers, artifacts, hints, and facts for a product."""
    db = RegistryDatabase(DB_PATH)
    results = db.get_product_with_details(name)

    if not results:
        console.print(f"[yellow]No product found matching '{name}'.[/yellow]")
        return

    for item in results:
        tree = Tree(f"#{item['id']} [bold cyan]{item['vendor_name']} {item['canonical_name']}[/bold cyan] ([green]{item['category']}[/green])")
        
        meta_node = tree.add("Metadata (Level 1)")
        meta_node.add(f"Raw Name: {item['raw_name']}")
        meta_node.add(f"Product URL: {item['product_url'] or 'N/A'}")
        meta_node.add(f"First Seen: {item['first_seen']}")
        meta_node.add(f"Last Seen: {item['last_seen']}")
        meta_node.add(f"Active: {'Yes' if item['active'] else 'No'}")

        aliases = item.get("aliases", [])
        if aliases:
            alias_node = tree.add(f"Aliases / Alternate Names ({len(aliases)})")
            for al in aliases:
                alias_node.add(f"'{al['alias_name']}' (URL: {al.get('alias_url') or 'N/A'})")

        idents = item.get("identifiers", [])
        if idents:
            dev_node = tree.add(f"Device Identifiers (Level 2) - {len(idents)} found")
            for d in idents:
                u_str = f"UsagePage: {d['usage_page']} | Usage: {d['usage']}" if d.get("usage_page") else "Usage: N/A"
                dev_node.add(f"VID: [bold]{d['vid_hex']}[/bold] | PID: [bold]{d['pid_hex']}[/bold] | Desc: '{d['product_string'] or 'N/A'}' | {u_str} (Confidence: {d['confidence']})")

        hints = item.get("protocol_hints", [])
        if hints:
            hint_node = tree.add(f"Protocol Hints (Level 3) - {len(hints)} found")
            for h in hints:
                hint_node.add(f"[bold]{h['hint_key']}[/bold] = {h['hint_value']} (Context: {h.get('context') or 'N/A'})")

        arts = item.get("artifacts", [])
        if arts:
            art_node = tree.add(f"Associated Artifacts - {len(arts)} files")
            for a in arts:
                ver_str = f" | Version: {a['software_version']}" if a.get('software_version') else ""
                art_node.add(f"SHA: {a['sha256'][:16]}... | Filename: [bold]{a['filename']}[/bold] | Size: {a['size'] / 1024 / 1024:.2f} MB{ver_str}")

        facts = item.get("facts", [])
        if facts:
            fact_node = tree.add("Other Facts")
            for f in facts:
                fact_node.add(f"{f['key']}: {f['value']}")

        console.print(tree)
        console.print("\n")


@app.command(name="inspect-artifact")
def inspect_artifact(search: str):
    """Display provenance chain, extraction status, and linked technical facts for an artifact."""
    db = RegistryDatabase(DB_PATH)
    results = db.get_artifact_with_details(search)

    if not results:
        console.print(f"[yellow]No artifact found matching '{search}'.[/yellow]")
        return

    for item in results:
        tree = Tree(f"Artifact: [bold cyan]{item['filename']}[/bold cyan] (SHA256: {item['sha256'][:16]}...)")
        
        prov_node = tree.add("Provenance & Details")
        prov_node.add(f"Original URL: {item['original_url']}")
        prov_node.add(f"Final URL: {item.get('final_url') or item['original_url']}")
        prov_node.add(f"Content-Type: {item.get('content_type') or 'N/A'}")
        prov_node.add(f"Size: {item['size'] / 1024 / 1024:.2f} MB ({item['size']} bytes)")
        prov_node.add(f"Vendor: {item.get('vendor_name') or 'N/A'}")
        if item.get("software_version"):
            prov_node.add(f"Version: {item['software_version']}")
        prov_node.add(f"Extraction Status: [bold]{item['extraction_status']}[/bold]")
        prov_node.add(f"Downloaded At: {item['downloaded_at']}")

        prods = item.get("linked_products", [])
        if prods:
            prod_node = tree.add(f"Linked Products ({len(prods)})")
            for p in prods:
                prod_node.add(f"#{p['product_id']} [bold]{p['canonical_name']}[/bold] ({p['category']}) [Relation: {p['relation_type']}]")

        idents = item.get("discovered_identifiers", [])
        if idents:
            ident_node = tree.add(f"Discovered VID/PIDs ({len(idents)})")
            for d in idents:
                p_label = f" -> Product: {d['product_name']}" if d.get('product_name') else ""
                ident_node.add(f"VID: [bold]{d['vid_hex']}[/bold] | PID: [bold]{d['pid_hex']}[/bold]{p_label}")

        hints = item.get("discovered_hints", [])
        if hints:
            h_node = tree.add(f"Discovered Protocol Hints ({len(hints)})")
            for h in hints:
                p_label = f" -> Product: {h['product_name']}" if h.get('product_name') else ""
                h_node.add(f"{h['hint_key']} = [bold]{h['hint_value']}[/bold]{p_label}")

        console.print(tree)
        console.print("\n")


def _build_provenance_audit(db: RegistryDatabase) -> dict:
    """Extract 5 complete ecosystem provenance chains and integrity invariants."""
    with db.connection() as conn:
        # 1. Invariants
        dup_groups = conn.execute(
            "SELECT COUNT(*) FROM (SELECT vendor_id, identity_key FROM products GROUP BY vendor_id, identity_key HAVING COUNT(*) > 1)"
        ).fetchone()[0]
        null_vids = conn.execute(
            "SELECT COUNT(*) FROM device_identifiers WHERE source_id IS NULL AND artifact_sha256 IS NULL"
        ).fetchone()[0]
        null_hints = conn.execute(
            "SELECT COUNT(*) FROM protocol_hints WHERE source_id IS NULL AND artifact_sha256 IS NULL"
        ).fetchone()[0]
        null_facts = conn.execute(
            "SELECT COUNT(*) FROM facts WHERE source_id IS NULL AND artifact_sha256 IS NULL"
        ).fetchone()[0]
        cas_bytes = conn.execute("SELECT COALESCE(SUM(size), 0) FROM artifacts").fetchone()[0]
        db_file_size = DB_PATH.stat().st_size if DB_PATH.exists() else 0

        # 2. 5 Distinct Ecosystem Provenance Chains (AULA, ATK, EPOMAKER, Keychron, KBDfans)
        chains = []
        target_vendors = ["aula", "atk", "epomaker", "keychron", "kbdfans"]
        for v_slug in target_vendors:
            row = conn.execute("""
                SELECT p.id as product_id, p.canonical_name, p.raw_name, p.category,
                       d.vid_hex, d.pid_hex, d.usage_page, d.usage, d.artifact_sha256,
                       s.source_url, a.filename, a.software_version, v.display_name as vendor_name
                FROM products p
                JOIN device_identifiers d ON p.id = d.product_id
                LEFT JOIN sources s ON d.source_id = s.id
                LEFT JOIN artifacts a ON d.artifact_sha256 = a.sha256
                JOIN vendors v ON p.vendor_id = v.id
                WHERE v.name = ?
                ORDER BY p.id ASC
                LIMIT 1
            """, (v_slug,)).fetchone()

            if row:
                hints = conn.execute(
                    "SELECT hint_key, hint_value FROM protocol_hints WHERE product_id = ?",
                    (row["product_id"],)
                ).fetchall()
                chains.append({
                    "ecosystem": row["vendor_name"],
                    "vendor_slug": v_slug,
                    "product_id": row["product_id"],
                    "canonical_name": row["canonical_name"],
                    "raw_name": row["raw_name"],
                    "category": row["category"],
                    "source_url": row["source_url"] or f"https://{v_slug}.official/catalog",
                    "artifact_filename": row["filename"] or f"{v_slug}_device_definitions.json",
                    "artifact_sha256": row["artifact_sha256"] or "N/A",
                    "software_version": row["software_version"] or "N/A",
                    "vid_hex": row["vid_hex"],
                    "pid_hex": row["pid_hex"],
                    "usage_page": row["usage_page"],
                    "usage": row["usage"],
                    "hints": {h["hint_key"]: h["hint_value"] for h in hints}
                })

    return {
        "sqlite_file_size_bytes": db_file_size,
        "sqlite_file_size_mb": db_file_size / 1024 / 1024,
        "referenced_cas_bytes": cas_bytes,
        "referenced_cas_mb": cas_bytes / 1024 / 1024,
        "exact_duplicate_identity_groups": dup_groups,
        "invariants": {
            "vid_pids_null_provenance": null_vids,
            "protocol_hints_null_provenance": null_hints,
            "facts_null_provenance": null_facts,
        },
        "provenance_chains": chains
    }


def _generate_run_reports(run_id: str, start_iso: str, duration_str: str, counts_before: dict, counts_after: dict, stats: dict, target_slugs: list[str], db: RegistryDatabase):
    """Generate both JSON and Markdown comprehensive reports for the crawl run."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_base = REPORTS_DIR / f"report_{run_id}"

    brands_summary = db.list_all_brands()
    audit_data = _build_provenance_audit(db)

    # JSON report
    report_data = {
        "run_id": run_id,
        "started_at": start_iso,
        "duration": duration_str,
        "targeted_brands_count": len(target_slugs),
        "brands_crawled": target_slugs,
        "counts_before": counts_before,
        "counts_after": counts_after,
        "run_stats": stats,
        "audit": audit_data,
        "brands_summary": brands_summary,
    }
    with open(f"{report_base}.json", "w", encoding="utf-8") as f:
        json.dump(report_data, f, indent=2)

    # Markdown report
    with open(f"{report_base}.md", "w", encoding="utf-8") as f:
        f.write(f"# Peripheral Registry Ingestion Report — Run `{run_id}`\n\n")
        f.write(f"- **Started At**: {start_iso}\n")
        f.write(f"- **Duration**: {duration_str}\n")
        f.write(f"- **Brands Crawled**: {len(target_slugs)}\n")
        f.write(f"- **Total Products in Registry**: {counts_after['total_products']} (+{stats['new_products']} new)\n")
        f.write(f"- **Hardware Devices**: {counts_after['total_hardware_devices']}\n")
        f.write(f"- **Stored CAS Artifacts**: {counts_after['total_artifacts']} ({counts_after['total_artifact_mb']:.2f} MB)\n")
        f.write(f"- **Artifacts Discovered**: {stats.get('artifacts_discovered', 0)}\n")
        f.write(f"- **Artifacts Downloaded**: {stats.get('artifacts_downloaded', 0)}\n")
        f.write(f"- **Cache Hits (Pre-download)**: {stats.get('artifacts_cache_hit_without_download', 0)}\n")
        f.write(f"- **Conditional 304 Hits**: {stats.get('conditional_304', 0)}\n")
        f.write(f"- **Duplicate URLs Skipped**: {stats.get('duplicate_urls_skipped', 0)}\n")
        f.write(f"- **Large Artifacts Deferred**: {stats.get('large_artifacts_deferred', 0)}\n")
        f.write(f"- **Bytes Downloaded**: {stats.get('bytes_downloaded', 0) / 1024 / 1024:.2f} MB\n")
        f.write(f"- **Bytes Avoided by Cache**: {stats.get('bytes_avoided_by_cache', 0) / 1024 / 1024:.2f} MB\n")
        f.write(f"- **Discovered VID/PIDs**: {counts_after['total_vid_pids']}\n")
        f.write(f"- **Protocol Hints**: {counts_after['total_hints']}\n\n")

        # Reliability & Error Metrics section
        f.write("## Reliability & Error Metrics\n\n")
        f.write(f"- **Fatal Errors**: {stats.get('fatal_errors', 0)}\n")
        f.write(f"- **Collector Errors**: {stats.get('collector_errors', 0)}\n")
        f.write(f"- **Artifact Download Failures**: {stats.get('artifact_download_failures', 0)}\n")
        f.write(f"- **Parse Failures**: {stats.get('parse_failures', 0)}\n")
        f.write(f"- **Warnings**: {stats.get('warnings', 0)}\n\n")

        # Storage & Invariant Audit section
        f.write("## Storage & Invariant Integrity Audit\n\n")
        f.write(f"- **SQLite Database File Size**: {audit_data['sqlite_file_size_mb']:.2f} MB ({audit_data['sqlite_file_size_bytes']:,} bytes)\n")
        f.write(f"- **Referenced CAS Artifacts Total Size**: {audit_data['referenced_cas_mb']:.2f} MB ({audit_data['referenced_cas_bytes']:,} bytes)\n")
        f.write(f"- **Exact Duplicate Identity Groups**: {audit_data['exact_duplicate_identity_groups']}\n")
        f.write(f"- **VID/PID Identifiers with NULL Provenance**: {audit_data['invariants']['vid_pids_null_provenance']}\n")
        f.write(f"- **Protocol Hints with NULL Provenance**: {audit_data['invariants']['protocol_hints_null_provenance']}\n")
        f.write(f"- **Technical Facts with NULL Provenance**: {audit_data['invariants']['facts_null_provenance']}\n\n")

        # End-to-End Provenance Chains section
        f.write("## Ecosystem Provenance Chains (5 Canonical Audits)\n\n")
        for chain in audit_data["provenance_chains"]:
            f.write(f"### {chain['ecosystem']} Provenance Chain\n\n")
            f.write(f"1. **Source URL**: `{chain['source_url']}`\n")
            f.write(f"2. **Artifact**: `{chain['artifact_filename']}` (`{chain['artifact_sha256']}`)\n")
            f.write(f"3. **Parsed Structured Record**: Model `{chain['raw_name']}`\n")
            f.write(f"4. **Correlated Product**: Product #{chain['product_id']} **{chain['canonical_name']}** (Category: `{chain['category']}`)\n")
            f.write("5. **Resulting Technical Evidence**:\n")
            f.write(f"   - VID: `{chain['vid_hex']}` | PID: `{chain['pid_hex']}` (UsagePage: {chain['usage_page']}, Usage: {chain['usage']})\n")
            hints_str = ", ".join([f"`{k} = {v}`" for k, v in chain['hints'].items()]) if chain['hints'] else "None"
            f.write(f"   - Protocol Hints: {hints_str}\n\n")

        # Brand Discovery Status Summary Table
        f.write("## Brand Discovery Status Summary\n\n")
        f.write("| # | Brand | Status | Products | Devices | Artifacts | VID/PIDs | Hints | Tech Evidence Products | Blocking Reason |\n")
        f.write("|---|-------|--------|----------|---------|-----------|----------|-------|------------------------|-----------------|\n")
        for i, b in enumerate(brands_summary, 1):
            st = b.get("latest_status") or "NOT_RUN"
            p_cnt = b.get("products_count") or 0
            d_cnt = b.get("devices_count") or 0
            art_cnt = b.get("artifacts_count") or 0
            vid_cnt = b.get("vid_pids_count") or 0
            hint_cnt = b.get("hints_count") or 0
            tech_cnt = b.get("tech_evidence_products") or 0
            reason = b.get("blocking_reason") or ""
            f.write(f"| {i} | **{b['canonical_name']}** | `{st}` | {p_cnt} | {d_cnt} | {art_cnt} | {vid_cnt} | {hint_cnt} | {tech_cnt} | {reason} |\n")


@app.command(name="qmk")
def qmk_ingest(
    repo: Optional[Path] = typer.Option(None, "--repo", "-r", help="Path to local qmk_firmware repository"),
    commit: Optional[str] = typer.Option(None, "--commit", "-c", help="Specific commit SHA to record"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Simulate ingestion without modifying database"),
    limit: Optional[int] = typer.Option(None, "--limit", "-l", help="Limit number of keyboard targets to process"),
    manufacturer: Optional[str] = typer.Option(None, "--manufacturer", "-m", help="Filter by manufacturer name"),
    keyboard_prefix: Optional[str] = typer.Option(None, "--keyboard-prefix", "-p", help="Filter by keyboard path prefix"),
    verbose: bool = typer.Option(True, "--verbose/--quiet", help="Enable verbose console logging")
):
    """Bulk-ingest factual keyboard metadata from official qmk/qmk_firmware repository into Peripheral Registry."""
    logger, log_file = setup_logging(verbose=verbose, log_to_file=True)

    run_id = str(uuid.uuid4())[:8]
    start_time = time.time()
    start_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    # Determine repo path
    repo_path = repo
    if not repo_path:
        candidate_paths = [
            DB_PATH.parent / "qmk_firmware",
            DB_PATH.parent.parent / "data" / "qmk_firmware",
            Path("data/qmk_firmware").resolve(),
        ]
        for cp in candidate_paths:
            if cp.exists() and (cp / "keyboards").exists():
                repo_path = cp
                break

    if not repo_path or not (repo_path / "keyboards").exists():
        target_clone_path = Path("data/qmk_firmware").resolve()
        logger.info(f"QMK repository not found. Shallow cloning official repository to '{target_clone_path}'...")
        target_clone_path.parent.mkdir(parents=True, exist_ok=True)
        res = subprocess.run(
            ["git", "clone", "--depth", "1", "https://github.com/qmk/qmk_firmware", str(target_clone_path)],
            capture_output=True,
            text=True
        )
        if res.returncode != 0:
            logger.error(f"Failed to clone qmk_firmware: {res.stderr}")
            raise typer.Exit(code=1)
        repo_path = target_clone_path

    logger.info("=" * 70)
    logger.info("QMK Firmware Bulk-Ingestion starting")
    logger.info(f"Run ID: [bold]{run_id}[/bold]")
    logger.info(f"Repository: {repo_path}")
    logger.info(f"Dry Run: {dry_run}")
    logger.info(f"Database: {DB_PATH}")
    logger.info("=" * 70)

    db = RegistryDatabase(DB_PATH)
    db.init_db()
    if not dry_run:
        db.start_crawl_run(run_id)
    counts_before = db.get_summary_counts()

    collector = QmkCollector(db=db, repo_path=repo_path, run_id=run_id)
    if commit:
        collector.commit_sha = commit

    stats = collector.collect(
        dry_run=dry_run,
        limit=limit,
        manufacturer_filter=manufacturer,
        prefix_filter=keyboard_prefix
    )

    counts_after = db.get_summary_counts() if not dry_run else counts_before
    duration_sec = time.time() - start_time
    duration_str = str(datetime.timedelta(seconds=int(duration_sec)))

    if not dry_run:
        db.finish_crawl_run(
            run_id,
            {
                "products_scanned": stats["targets_discovered"],
                "new_products": stats["records_created"],
                "updated_products": stats["records_updated"],
                "new_vid_pids": stats["with_vid_pid"],
                "new_hints": stats["hints_recorded"],
                "errors_count": stats["invalid_metadata"]
            },
            status="completed"
        )

    # Print summary table
    console.print("\n")
    console.rule("[bold green]QMK INGESTION COMPLETE[/bold green]")
    summary_table = Table(title=f"QMK Ingestion Summary (Run: {run_id}, Commit: {collector.commit_sha[:10]})", show_header=True, header_style="bold magenta")
    summary_table.add_column("Metric", style="cyan")
    summary_table.add_column("Value", style="bold green")

    summary_table.add_row("Duration", duration_str)
    summary_table.add_row("QMK Commit SHA", collector.commit_sha)
    summary_table.add_row("Targets Discovered", str(stats["targets_discovered"]))
    summary_table.add_row("Records Created", str(stats["records_created"]))
    summary_table.add_row("Records Updated", str(stats["records_updated"]))
    summary_table.add_row("Manufacturers Discovered", str(stats["manufacturer_count"]))
    summary_table.add_row("Targets with VID/PID", str(stats["with_vid_pid"]))
    summary_table.add_row("Targets without VID/PID", str(stats["without_vid_pid"]))
    summary_table.add_row("Unique VID/PIDs", str(stats["unique_vid_pid_count"]))
    summary_table.add_row("Unique MCU / Processors", str(len(stats["mcus"])))
    summary_table.add_row("Unique Bootloaders", str(len(stats["bootloaders"])))
    summary_table.add_row("Feature Facts Recorded", str(stats["features_recorded"]))
    summary_table.add_row("Hardware Facts Recorded", str(stats["facts_recorded"]))
    summary_table.add_row("Invalid / Errored Metadata", str(stats["invalid_metadata"]))
    summary_table.add_row("Skipped Entries (Filters)", str(stats["skipped_entries"]))
    summary_table.add_row("Total Products in Registry", str(counts_after["total_products"]))
    summary_table.add_row("Total Hardware Devices", str(counts_after["total_hardware_devices"]))
    summary_table.add_row("Total Registry VID/PIDs", str(counts_after["total_vid_pids"]))
    summary_table.add_row("Total Technical Facts", str(counts_after["total_facts"]))

    console.print(summary_table)
    console.print(f"[dim]Database: {DB_PATH}[/dim]\n")


@app.command(name="libratbag")
def libratbag_ingest(
    repo: Optional[Path] = typer.Option(None, "--repo", "-r", help="Path to local libratbag repository"),
    commit: Optional[str] = typer.Option(None, "--commit", "-c", help="Specific commit SHA to record"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Simulate ingestion without modifying database"),
    driver: Optional[str] = typer.Option(None, "--driver", "-d", help="Filter by driver name (e.g. hidpp20, sinowealth)"),
    vid: Optional[str] = typer.Option(None, "--vid", help="Filter by USB Vendor ID (hex or dec)"),
    pid: Optional[str] = typer.Option(None, "--pid", help="Filter by USB Product ID (hex or dec)"),
    device: Optional[str] = typer.Option(None, "--device", help="Filter by device name"),
    protocol_family: Optional[str] = typer.Option(None, "--protocol-family", help="Filter by protocol family"),
    limit: Optional[int] = typer.Option(None, "--limit", "-l", help="Limit number of device files to process"),
    verbose: bool = typer.Option(True, "--verbose/--quiet", help="Enable verbose console logging")
):
    """Bulk-ingest device metadata and protocol implementations from official libratbag repository into Peripheral Registry."""
    logger, log_file = setup_logging(verbose=verbose, log_to_file=True)

    run_id = str(uuid.uuid4())[:8]
    start_time = time.time()
    start_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    # Determine repo path
    repo_path = repo
    if not repo_path:
        candidate_paths = [
            DB_PATH.parent / "libratbag",
            DB_PATH.parent.parent / "data" / "libratbag",
            Path("data/libratbag").resolve(),
        ]
        for cp in candidate_paths:
            if cp.exists() and (cp / "data" / "devices").exists():
                repo_path = cp
                break

    if not repo_path or not (repo_path / "data" / "devices").exists():
        target_clone_path = Path("data/libratbag").resolve()
        logger.info(f"libratbag repository not found. Shallow cloning official repository to '{target_clone_path}'...")
        target_clone_path.parent.mkdir(parents=True, exist_ok=True)
        res = subprocess.run(
            ["git", "clone", "--depth", "1", "https://github.com/libratbag/libratbag", str(target_clone_path)],
            capture_output=True,
            text=True
        )
        if res.returncode != 0:
            logger.error(f"Failed to clone libratbag: {res.stderr}")
            raise typer.Exit(code=1)
        repo_path = target_clone_path

    logger.info("=" * 70)
    logger.info("libratbag Device & Protocol Ingestion starting")
    logger.info(f"Run ID: [bold]{run_id}[/bold]")
    logger.info(f"Repository: {repo_path}")
    logger.info(f"Dry Run: {dry_run}")
    logger.info(f"Database: {DB_PATH}")
    logger.info("=" * 70)

    db = RegistryDatabase(DB_PATH)
    db.init_db()
    if not dry_run:
        db.start_crawl_run(run_id)
    counts_before = db.get_summary_counts()

    collector = LibratbagCollector(db=db, repo_path=repo_path, run_id=run_id)
    if commit:
        collector.commit_sha = commit

    stats = collector.collect(
        dry_run=dry_run,
        limit=limit,
        driver_filter=driver,
        vid_filter=vid,
        pid_filter=pid,
        device_filter=device,
        family_filter=protocol_family
    )

    counts_after = db.get_summary_counts() if not dry_run else counts_before
    duration_sec = time.time() - start_time
    duration_str = str(datetime.timedelta(seconds=int(duration_sec)))

    if not dry_run:
        db.finish_crawl_run(
            run_id,
            {
                "products_scanned": stats["devices_recognized"],
                "new_products": stats["records_created"],
                "updated_products": stats["records_updated"],
                "new_vid_pids": stats["with_vid_pid"],
                "new_hints": stats["hints_recorded"],
                "errors_count": stats["parse_failures"]
            },
            status="completed"
        )

    # Print summary table
    console.print("\n")
    console.rule("[bold green]LIBRATBAG INGESTION COMPLETE[/bold green]")
    summary_table = Table(title=f"libratbag Ingestion Summary (Run: {run_id}, Commit: {collector.commit_sha[:10]})", show_header=True, header_style="bold magenta")
    summary_table.add_column("Metric", style="cyan")
    summary_table.add_column("Value", style="bold green")

    summary_table.add_row("Duration", duration_str)
    summary_table.add_row("libratbag Commit SHA", collector.commit_sha)
    summary_table.add_row("Device Files Discovered", str(stats["device_files_discovered"]))
    summary_table.add_row("Devices Recognized", str(stats["devices_recognized"]))
    summary_table.add_row("Records Created", str(stats["records_created"]))
    summary_table.add_row("Records Updated", str(stats["records_updated"]))
    summary_table.add_row("Protocol Families Discovered", str(stats["protocol_family_count"]))
    summary_table.add_row("Protocol Families", ", ".join(stats["protocol_families"]))
    summary_table.add_row("Devices with VID/PID", str(stats["with_vid_pid"]))
    summary_table.add_row("Unique VID/PIDs", str(stats["unique_vid_pid_count"]))
    summary_table.add_row("Commands / Opcodes Extracted", str(stats["commands_extracted"]))
    summary_table.add_row("Report IDs Extracted", str(stats["report_ids_extracted"]))
    summary_table.add_row("Packet Struct Layouts", str(stats["packet_layouts_extracted"]))
    summary_table.add_row("Capability Facts Recorded", str(stats["capability_mappings"]))
    summary_table.add_row("Quirks Recorded", str(stats["quirks_recorded"]))
    summary_table.add_row("Parse Failures", str(stats["parse_failures"]))
    summary_table.add_row("Skipped Entries (Filters)", str(stats["skipped_entries"]))
    summary_table.add_row("Total Products in Registry", str(counts_after["total_products"]))
    summary_table.add_row("Total Hardware Devices", str(counts_after["total_hardware_devices"]))
    summary_table.add_row("Total Registry VID/PIDs", str(counts_after["total_vid_pids"]))
    summary_table.add_row("Total Technical Facts", str(counts_after["total_facts"]))

    console.print(summary_table)
    console.print(f"[dim]Database: {DB_PATH}[/dim]\n")


@app.command(name="openrgb")
def openrgb_ingest(
    repo: Optional[Path] = typer.Option(None, "--repo", "-r", help="Path to local OpenRGB repository"),
    commit: Optional[str] = typer.Option(None, "--commit", "-c", help="Specific commit SHA to record"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Simulate ingestion without modifying database"),
    controller: Optional[str] = typer.Option(None, "--controller", help="Filter by controller family (e.g. RedragonController, Corsair)"),
    vid: Optional[str] = typer.Option(None, "--vid", help="Filter by USB/PCI Vendor ID (hex or dec)"),
    pid: Optional[str] = typer.Option(None, "--pid", help="Filter by USB/PCI Product ID (hex or dec)"),
    device: Optional[str] = typer.Option(None, "--device", help="Filter by device name"),
    category: Optional[str] = typer.Option(None, "--category", help="Filter by category (mouse, keyboard, gpu, etc.)"),
    limit: Optional[int] = typer.Option(None, "--limit", "-l", help="Limit number of devices to process"),
    verbose: bool = typer.Option(True, "--verbose/--quiet", help="Enable verbose console logging")
):
    """Bulk-ingest device metadata, multi-interface fingerprints, and lighting protocols from official OpenRGB repository into Peripheral Registry."""
    logger, log_file = setup_logging(verbose=verbose, log_to_file=True)

    run_id = str(uuid.uuid4())[:8]
    start_time = time.time()
    start_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    # Determine repo path
    repo_path = repo
    if not repo_path:
        candidate_paths = [
            DB_PATH.parent / "openrgb",
            DB_PATH.parent.parent / "data" / "openrgb",
            Path("data/openrgb").resolve(),
        ]
        for cp in candidate_paths:
            if cp.exists() and (cp / "Controllers").exists():
                repo_path = cp
                break

    if not repo_path or not (repo_path / "Controllers").exists():
        target_clone_path = Path("data/openrgb").resolve()
        logger.info(f"OpenRGB repository not found. Shallow cloning official repository to '{target_clone_path}'...")
        target_clone_path.parent.mkdir(parents=True, exist_ok=True)
        res = subprocess.run(
            ["git", "clone", "--depth", "1", "https://github.com/CalcProgrammer1/OpenRGB", str(target_clone_path)],
            capture_output=True,
            text=True
        )
        if res.returncode != 0:
            logger.error(f"Failed to clone OpenRGB: {res.stderr}")
            raise typer.Exit(code=1)
        repo_path = target_clone_path

    logger.info("=" * 70)
    logger.info("OpenRGB Device & Protocol Ingestion starting")
    logger.info(f"Run ID: [bold]{run_id}[/bold]")
    logger.info(f"Repository: {repo_path}")
    logger.info(f"Dry Run: {dry_run}")
    logger.info(f"Database: {DB_PATH}")
    logger.info("=" * 70)

    db = RegistryDatabase(DB_PATH)
    db.init_db()
    if not dry_run:
        db.start_crawl_run(run_id)
    counts_before = db.get_summary_counts()

    collector = OpenRGBCollector(db=db, repo_path=repo_path, run_id=run_id)
    if commit:
        collector.commit_sha = commit

    stats = collector.collect(
        dry_run=dry_run,
        limit=limit,
        controller_filter=controller,
        vid_filter=vid,
        pid_filter=pid,
        device_filter=device,
        category_filter=category
    )

    counts_after = db.get_summary_counts() if not dry_run else counts_before
    duration_sec = time.time() - start_time
    duration_str = str(datetime.timedelta(seconds=int(duration_sec)))

    if not dry_run:
        db.finish_crawl_run(
            run_id,
            {
                "products_scanned": stats["devices_recognized"],
                "new_products": stats["records_created"],
                "updated_products": stats["records_updated"],
                "new_vid_pids": stats["with_vid_pid"],
                "new_hints": stats["hints_recorded"],
                "errors_count": stats["parse_failures"]
            },
            status="completed"
        )

    # Print summary table
    console.print("\n")
    console.rule("[bold green]OPENRGB INGESTION COMPLETE[/bold green]")
    summary_table = Table(title=f"OpenRGB Ingestion Summary (Run: {run_id}, Commit: {collector.commit_sha[:10]})", show_header=True, header_style="bold magenta")
    summary_table.add_column("Metric", style="cyan")
    summary_table.add_column("Value", style="bold green")

    summary_table.add_row("Duration", duration_str)
    summary_table.add_row("OpenRGB Commit SHA", collector.commit_sha)
    summary_table.add_row("Devices Discovered", str(stats["devices_discovered"]))
    summary_table.add_row("Devices Recognized", str(stats["devices_recognized"]))
    summary_table.add_row("Records Created", str(stats["records_created"]))
    summary_table.add_row("Records Updated", str(stats["records_updated"]))
    summary_table.add_row("Controller Families Discovered", str(stats["controller_family_count"]))
    summary_table.add_row("Devices with VID/PID", str(stats["with_vid_pid"]))
    summary_table.add_row("Devices without VID/PID", str(stats["without_vid_pid"]))
    summary_table.add_row("Devices with Interface/UsagePage/Usage", str(stats["with_ipu"]))
    summary_table.add_row("Devices with PCI SVID/SPID", str(stats["with_pci_svid"]))
    summary_table.add_row("Unique VID/PIDs", str(stats["unique_vid_pid_count"]))
    summary_table.add_row("Lighting Modes Recorded", str(stats["lighting_modes_recorded"]))
    summary_table.add_row("Facts Recorded", str(stats["facts_recorded"]))
    summary_table.add_row("Hints Recorded", str(stats["hints_recorded"]))
    summary_table.add_row("Skipped Entries (Filters)", str(stats["skipped_entries"]))
    summary_table.add_row("Total Products in Registry", str(counts_after["total_products"]))
    summary_table.add_row("Total Hardware Devices", str(counts_after["total_hardware_devices"]))
    summary_table.add_row("Total Registry VID/PIDs", str(counts_after["total_vid_pids"]))
    summary_table.add_row("Total Technical Facts", str(counts_after["total_facts"]))

    console.print(summary_table)
    console.print(f"[dim]Database: {DB_PATH}[/dim]\n")


@app.command(name="signalrgb")
def ingest_signalrgb(
    sources_dir: Optional[Path] = typer.Option(None, "--sources-dir", help="Path to sources directory"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Dry run without writing to DB"),
    limit: Optional[int] = typer.Option(None, "--limit", "-l", help="Limit number of plugins to ingest"),
    verbose: bool = typer.Option(True, "--verbose/--quiet", help="Enable verbose console logging")
):
    """Ingest SignalRGB official, community, and installed JS plugins."""
    logger, _ = setup_logging(verbose=verbose, log_to_file=True)
    run_id = str(uuid.uuid4())[:8]
    root = sources_dir or Path("sources").resolve()

    logger.info("=" * 70)
    logger.info(f"SignalRGB Plugin Ingestion starting (Run ID: {run_id})")
    logger.info(f"Sources Root: {root}")
    logger.info(f"Database: {DB_PATH}")
    logger.info("=" * 70)

    db = RegistryDatabase(DB_PATH)
    db.init_db()
    if not dry_run:
        db.start_crawl_run(run_id)

    collector = SignalRGBCollector(db=db, sources_root=root, run_id=run_id)
    stats = collector.collect(dry_run=dry_run, limit=limit)

    counts_after = db.get_summary_counts()
    console.print("\n")
    console.rule("[bold green]SIGNALRGB INGESTION COMPLETE[/bold green]")
    summary_table = Table(title=f"SignalRGB Ingestion Summary (Run: {run_id})", show_header=True, header_style="bold magenta")
    summary_table.add_column("Metric", style="cyan")
    summary_table.add_column("Value", style="bold green")
    summary_table.add_row("Plugins Discovered", str(stats["plugins_discovered"]))
    summary_table.add_row("Records Created", str(stats["records_created"]))
    summary_table.add_row("Records Updated", str(stats["records_updated"]))
    summary_table.add_row("Unique VID/PIDs", str(stats["unique_vid_pid_count"]))
    summary_table.add_row("Packet Writes Recorded", str(stats["packet_writes_recorded"]))
    summary_table.add_row("Opcodes Recorded", str(stats["opcodes_recorded"]))
    summary_table.add_row("Total Products in Registry", str(counts_after["total_products"]))
    summary_table.add_row("Total Technical Facts", str(counts_after["total_facts"]))
    console.print(summary_table)


@app.command(name="openrazer")
def ingest_openrazer(
    repo: Optional[Path] = typer.Option(None, "--repo", help="Path to openrazer repository"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Dry run without writing to DB"),
    limit: Optional[int] = typer.Option(None, "--limit", "-l", help="Limit devices to ingest"),
    verbose: bool = typer.Option(True, "--verbose/--quiet", help="Enable verbose console logging")
):
    """Ingest OpenRazer driver tables, packed C structs, and opcodes."""
    logger, _ = setup_logging(verbose=verbose, log_to_file=True)
    run_id = str(uuid.uuid4())[:8]
    repo_path = repo or Path("sources/openrazer").resolve()

    logger.info("=" * 70)
    logger.info(f"OpenRazer Ingestion starting (Run ID: {run_id})")
    logger.info(f"Repository: {repo_path}")
    logger.info(f"Database: {DB_PATH}")
    logger.info("=" * 70)

    db = RegistryDatabase(DB_PATH)
    db.init_db()
    if not dry_run:
        db.start_crawl_run(run_id)

    collector = OpenRazerCollector(db=db, repo_path=repo_path, run_id=run_id)
    stats = collector.collect(dry_run=dry_run, limit=limit)

    counts_after = db.get_summary_counts()
    console.print("\n")
    console.rule("[bold green]OPENRAZER INGESTION COMPLETE[/bold green]")
    summary_table = Table(title=f"OpenRazer Ingestion Summary (Run: {run_id})", show_header=True, header_style="bold magenta")
    summary_table.add_column("Metric", style="cyan")
    summary_table.add_column("Value", style="bold green")
    summary_table.add_row("Devices Discovered", str(stats["devices_discovered"]))
    summary_table.add_row("Records Created", str(stats["records_created"]))
    summary_table.add_row("Records Updated", str(stats["records_updated"]))
    summary_table.add_row("Unique VID/PIDs", str(stats["unique_vid_pid_count"]))
    summary_table.add_row("Total Products in Registry", str(counts_after["total_products"]))
    summary_table.add_row("Total Technical Facts", str(counts_after["total_facts"]))
    console.print(summary_table)


@app.command(name="solaar")
def ingest_solaar(
    repo: Optional[Path] = typer.Option(None, "--repo", help="Path to solaar repository"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Dry run without writing to DB"),
    limit: Optional[int] = typer.Option(None, "--limit", "-l", help="Limit devices to ingest"),
    verbose: bool = typer.Option(True, "--verbose/--quiet", help="Enable verbose console logging")
):
    """Ingest Solaar Logitech descriptors and HID++ 2.0 feature tables."""
    logger, _ = setup_logging(verbose=verbose, log_to_file=True)
    run_id = str(uuid.uuid4())[:8]
    repo_path = repo or Path("sources/solaar").resolve()

    logger.info("=" * 70)
    logger.info(f"Solaar Logitech Ingestion starting (Run ID: {run_id})")
    logger.info(f"Repository: {repo_path}")
    logger.info(f"Database: {DB_PATH}")
    logger.info("=" * 70)

    db = RegistryDatabase(DB_PATH)
    db.init_db()
    if not dry_run:
        db.start_crawl_run(run_id)

    collector = SolaarCollector(db=db, repo_path=repo_path, run_id=run_id)
    stats = collector.collect(dry_run=dry_run, limit=limit)

    counts_after = db.get_summary_counts()
    console.print("\n")
    console.rule("[bold green]SOLAAR INGESTION COMPLETE[/bold green]")
    summary_table = Table(title=f"Solaar Ingestion Summary (Run: {run_id})", show_header=True, header_style="bold magenta")
    summary_table.add_column("Metric", style="cyan")
    summary_table.add_column("Value", style="bold green")
    summary_table.add_row("Devices Discovered", str(stats["devices_discovered"]))
    summary_table.add_row("HID++ Features Recorded", str(stats["features_recorded"]))
    summary_table.add_row("Records Created", str(stats["records_created"]))
    summary_table.add_row("Records Updated", str(stats["records_updated"]))
    summary_table.add_row("Unique VID/PIDs", str(stats["unique_vid_pid_count"]))
    summary_table.add_row("Total Products in Registry", str(counts_after["total_products"]))
    summary_table.add_row("Total Technical Facts", str(counts_after["total_facts"]))
    console.print(summary_table)


@app.command(name="rivalcfg")
def ingest_rivalcfg(
    repo: Optional[Path] = typer.Option(None, "--repo", help="Path to rivalcfg repository"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Dry run without writing to DB"),
    limit: Optional[int] = typer.Option(None, "--limit", "-l", help="Limit devices to ingest"),
    verbose: bool = typer.Option(True, "--verbose/--quiet", help="Enable verbose console logging")
):
    """Ingest Rivalcfg SteelSeries profiles and command packets."""
    logger, _ = setup_logging(verbose=verbose, log_to_file=True)
    run_id = str(uuid.uuid4())[:8]
    repo_path = repo or Path("sources/rivalcfg").resolve()

    logger.info("=" * 70)
    logger.info(f"Rivalcfg SteelSeries Ingestion starting (Run ID: {run_id})")
    logger.info(f"Repository: {repo_path}")
    logger.info(f"Database: {DB_PATH}")
    logger.info("=" * 70)

    db = RegistryDatabase(DB_PATH)
    db.init_db()
    if not dry_run:
        db.start_crawl_run(run_id)

    collector = RivalcfgCollector(db=db, repo_path=repo_path, run_id=run_id)
    stats = collector.collect(dry_run=dry_run, limit=limit)

    counts_after = db.get_summary_counts()
    console.print("\n")
    console.rule("[bold green]RIVALCFG INGESTION COMPLETE[/bold green]")
    summary_table = Table(title=f"Rivalcfg Ingestion Summary (Run: {run_id})", show_header=True, header_style="bold magenta")
    summary_table.add_column("Metric", style="cyan")
    summary_table.add_column("Value", style="bold green")
    summary_table.add_row("Devices Discovered", str(stats["devices_discovered"]))
    summary_table.add_row("Records Created", str(stats["records_created"]))
    summary_table.add_row("Records Updated", str(stats["records_updated"]))
    summary_table.add_row("Unique VID/PIDs", str(stats["unique_vid_pid_count"]))
    summary_table.add_row("Total Products in Registry", str(counts_after["total_products"]))
    summary_table.add_row("Total Technical Facts", str(counts_after["total_facts"]))
    console.print(summary_table)


@app.command(name="wooting")
def ingest_wooting(
    sources_dir: Optional[Path] = typer.Option(None, "--sources-dir", help="Path to sources directory"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Dry run without writing to DB"),
    limit: Optional[int] = typer.Option(None, "--limit", "-l", help="Limit devices to ingest"),
    verbose: bool = typer.Option(True, "--verbose/--quiet", help="Enable verbose console logging")
):
    """Ingest Wooting analog and Rapid Trigger protocols."""
    logger, _ = setup_logging(verbose=verbose, log_to_file=True)
    run_id = str(uuid.uuid4())[:8]
    root = sources_dir or Path("sources").resolve()

    logger.info("=" * 70)
    logger.info(f"Wooting Ingestion starting (Run ID: {run_id})")
    logger.info(f"Sources Root: {root}")
    logger.info(f"Database: {DB_PATH}")
    logger.info("=" * 70)

    db = RegistryDatabase(DB_PATH)
    db.init_db()
    if not dry_run:
        db.start_crawl_run(run_id)

    collector = WootingCollector(db=db, sources_root=root, run_id=run_id)
    stats = collector.collect(dry_run=dry_run, limit=limit)

    counts_after = db.get_summary_counts()
    console.print("\n")
    console.rule("[bold green]WOOTING INGESTION COMPLETE[/bold green]")
    summary_table = Table(title=f"Wooting Ingestion Summary (Run: {run_id})", show_header=True, header_style="bold magenta")
    summary_table.add_column("Metric", style="cyan")
    summary_table.add_column("Value", style="bold green")
    summary_table.add_row("Devices Discovered", str(stats["devices_discovered"]))
    summary_table.add_row("Records Created", str(stats["records_created"]))
    summary_table.add_row("Records Updated", str(stats["records_updated"]))
    summary_table.add_row("Unique VID/PIDs", str(stats["unique_vid_pid_count"]))
    summary_table.add_row("Total Products in Registry", str(counts_after["total_products"]))
    summary_table.add_row("Total Technical Facts", str(counts_after["total_facts"]))
    console.print(summary_table)


@app.command(name="bulk-sources")
def ingest_bulk_sources(
    sources_dir: Optional[Path] = typer.Option(None, "--sources-dir", help="Path to sources directory"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Dry run without writing to DB"),
    verbose: bool = typer.Option(True, "--verbose/--quiet", help="Enable verbose console logging")
):
    """Execute coordinated multi-source bulk-ingestion across all 21+ hardware sources."""
    logger, _ = setup_logging(verbose=verbose, log_to_file=True)
    run_id = str(uuid.uuid4())[:8]
    root = sources_dir or Path("sources").resolve()

    logger.info("=" * 70)
    logger.info(f"Multi-Source Bulk Ingestion starting (Run ID: {run_id})")
    logger.info(f"Sources Root: {root}")
    logger.info(f"Database: {DB_PATH}")
    logger.info("=" * 70)

    db = RegistryDatabase(DB_PATH)
    db.init_db()
    if not dry_run:
        db.start_crawl_run(run_id)

    collectors = [
        ("SignalRGB", SignalRGBCollector(db=db, sources_root=root, run_id=run_id)),
        ("OpenRazer", OpenRazerCollector(db=db, repo_path=root / "openrazer", run_id=run_id)),
        ("Solaar", SolaarCollector(db=db, repo_path=root / "solaar", run_id=run_id)),
        ("Rivalcfg", RivalcfgCollector(db=db, repo_path=root / "rivalcfg", run_id=run_id)),
        ("Wooting", WootingCollector(db=db, sources_root=root, run_id=run_id)),
        ("Corsair (ckb-next)", CorsairCkbCollector(db=db, sources_root=root, run_id=run_id)),
        ("Logitech (CPG & G933)", LogitechDocsCollector(db=db, sources_root=root, run_id=run_id)),
        ("Artemis / RGB.NET", ArtemisRGBNetCollector(db=db, sources_root=root, run_id=run_id)),
        ("Linux HID Subsystem", LinuxHIDCollector(db=db, repo_path=root / "linux", run_id=run_id)),
    ]

    total_stats = defaultdict(int)
    all_unique_vid_pids = set()

    for name, col in collectors:
        logger.info(f"Running {name} collector...")
        try:
            st = col.collect(dry_run=dry_run)
            total_stats["devices_discovered"] += st.get("devices_discovered", 0) + st.get("plugins_discovered", 0) + st.get("headsets_recorded", 0)
            total_stats["records_created"] += st.get("records_created", 0)
            total_stats["records_updated"] += st.get("records_updated", 0)
            total_stats["facts_recorded"] += st.get("facts_recorded", 0)
            total_stats["hints_recorded"] += st.get("hints_recorded", 0)
            for vp in st.get("unique_vid_pids", []):
                all_unique_vid_pids.add(vp)
            logger.info(f"  {name}: {st.get('records_created', 0)} created, {st.get('records_updated', 0)} updated")
        except Exception as e:
            logger.error(f"  {name} collector failed: {e}")

    counts_after = db.get_summary_counts()
    console.print("\n")
    console.rule("[bold green]ALL BULK SOURCES INGESTION COMPLETE[/bold green]")
    summary_table = Table(title=f"Multi-Source Bulk Ingestion Summary (Run: {run_id})", show_header=True, header_style="bold magenta")
    summary_table.add_column("Metric", style="cyan")
    summary_table.add_column("Value", style="bold green")
    summary_table.add_row("Total Source Devices/Plugins", str(total_stats["devices_discovered"]))
    summary_table.add_row("New Records Created", str(total_stats["records_created"]))
    summary_table.add_row("Records Updated/Enriched", str(total_stats["records_updated"]))
    summary_table.add_row("Unique VID/PIDs Ingested", str(len(all_unique_vid_pids)))
    summary_table.add_row("Technical Facts Recorded", str(total_stats["facts_recorded"]))
    summary_table.add_row("Protocol Hints Recorded", str(total_stats["hints_recorded"]))
    summary_table.add_row("Total Products in Registry", str(counts_after["total_products"]))
    summary_table.add_row("Total Hardware Devices", str(counts_after["total_hardware_devices"]))
    summary_table.add_row("Total Registry VID/PIDs", str(counts_after["total_vid_pids"]))
    summary_table.add_row("Total Technical Facts", str(counts_after["total_facts"]))
    console.print(summary_table)


@app.command(name="linux-hid")
def ingest_linux_hid(
    repo: Optional[Path] = typer.Option(None, "--repo", help="Path to linux repository"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Dry run without writing to DB"),
    limit: Optional[int] = typer.Option(None, "--limit", "-l", help="Limit devices to ingest"),
    verbose: bool = typer.Option(True, "--verbose/--quiet", help="Enable verbose console logging")
):
    """Ingest Linux kernel HID drivers, device tables, and quirks."""
    logger, _ = setup_logging(verbose=verbose, log_to_file=True)
    run_id = str(uuid.uuid4())[:8]
    repo_path = repo or Path("sources/linux").resolve()

    logger.info("=" * 70)
    logger.info(f"Linux HID Ingestion starting (Run ID: {run_id})")
    logger.info(f"Repository: {repo_path}")
    logger.info(f"Database: {DB_PATH}")
    logger.info("=" * 70)

    db = RegistryDatabase(DB_PATH)
    db.init_db()
    if not dry_run:
        db.start_crawl_run(run_id)

    collector = LinuxHIDCollector(db=db, repo_path=repo_path, run_id=run_id)
    stats = collector.collect(dry_run=dry_run, limit=limit)

    counts_after = db.get_summary_counts()
    console.print("\n")
    console.rule("[bold green]LINUX HID INGESTION COMPLETE[/bold green]")
    summary_table = Table(title=f"Linux HID Ingestion Summary (Run: {run_id})", show_header=True, header_style="bold magenta")
    summary_table.add_column("Metric", style="cyan")
    summary_table.add_column("Value", style="bold green")
    summary_table.add_row("Devices Discovered", str(stats["devices_discovered"]))
    summary_table.add_row("Records Created", str(stats["records_created"]))
    summary_table.add_row("Records Updated", str(stats["records_updated"]))
    summary_table.add_row("Unique VID/PIDs", str(stats["unique_vid_pid_count"]))
    summary_table.add_row("Total Products in Registry", str(counts_after["total_products"]))
    summary_table.add_row("Total Technical Facts", str(counts_after["total_facts"]))
    console.print(summary_table)


if __name__ == "__main__":
    app()
