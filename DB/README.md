# Peripheral Registry Ingest (`peripheral-registry-ingest`)

Autonomous crawler and static inspection pipeline designed for accumulating a structured staging database of computer peripherals (keyboards, mice, official drivers/software, VID/PID, software versions, protocol hints, and provenance metadata).

---

## Key Principles & Safety Invariant

> [!IMPORTANT]
> **Zero Execution Invariant**: This ingestion pipeline **NEVER** executes downloaded `.exe`, `.msi`, `.sys`, `.dll`, or batch files. It solely performs static inspection (safe decompression, byte hashing, string scanning, AST/JSON/INF parsing).

- **Strict Provenance**: Every discovered VID/PID, protocol hint, and specification is linked to its exact source URL, HTTP status, and artifact SHA-256 digest.
- **Content-Addressed Storage (CAS)**: Downloaded artifacts are stored by SHA-256 hash in `artifacts/<prefix>/<sha256>`. Duplicate downloads across different product models are eliminated.
- **Resilient Network Layer**: Tier 1 fast HTTP fetching with `curl_cffi` (Chrome 120 TLS fingerprint impersonation) combined with Tier 2 `playwright-stealth` headless browser fallback for Cloudflare Turnstile / JS challenge resolution.

---

## Evidence Hierarchy

| Level | Classification | Description | Examples |
|---|---|---|---|
| **Level 1** | `METADATA` | Brand, model title, official product URL, driver download URL, version, release date | `AULA`, `Hero 84 HE`, `v3.2.14` |
| **Level 2** | `DEVICE_IDENTITY` | Hardware identifiers: Vendor ID, Product ID, Usage Page, Usage, Connection | `VID 0x372E`, `PID 0x103E`, `UsagePage 0xFF60` |
| **Level 3** | `PROTOCOL_HINT` | Protocol engine hints, SDK module names, command signatures, MCU markers | `sdkModuleName: "bytech"`, `CMD_GET_REPORT` |
| **Level 4** | `PROTOCOL_FACT` | Packet layout candidate saved for manual verification | Byte stream layout |
| **Level 5** | `HARDWARE_VERIFIED` | Hardware-tested in the physical Peripheral app (*never auto-promoted by crawler*) | Verified hardware support |

---

## Quick Start with `run_ingest.bat`

Double-click `run_ingest.bat` in Windows Explorer or run from CMD / PowerShell.

The script automatically:
1. Detects its directory (`cd /d "%~dp0"`).
2. Verifies Python 3.12+ installation.
3. Automatically sets up `.venv` if not present.
4. Installs required dependencies and Chromium browser binaries (`playwright install chromium`).
5. Displays an interactive CLI menu:

```text
============================================================
        PERIPHERAL REGISTRY INGESTION PIPELINE
============================================================
[1] Full crawl (all vendors, full downloads and static scanning)
[2] Metadata only (crawl portals without downloading large files)
[3] AULA only
[4] ATK / VXE only
[5] EPOMAKER only
[6] Keychron only
[7] Show Status and Summary
[8] List Discovered Products
[9] Exit
============================================================
Select option [1-9]:
```

---

## Command Line Interface (CLI)

You can also run the tool directly using the Python module:

```bash
# Full crawl with ultra-verbose logging
python -m ingest.main run --verbose

# Run specific vendors only
python -m ingest.main run --vendor aula --vendor keychron

# Metadata-only crawl (skip binary downloads)
python -m ingest.main run --metadata-only

# Check staging database counts
python -m ingest.main status

# List recently discovered products
python -m ingest.main list-new

# Inspect full provenance and facts for a specific model
python -m ingest.main inspect-product "Hero 84 HE"
```

---

## Project Structure

```text
peripheral-registry-ingest/
│
├── run_ingest.bat              # One-click Windows runner
├── requirements.txt            # Python dependencies
├── README.md                   # Complete documentation
├── .gitignore                  # Git ignore rules
│
├── ingest/                     # Python source package
│   ├── main.py                 # CLI entry point (Typer / Rich)
│   ├── config.py               # Paths, limits, timeout configurations
│   ├── logging_setup.py        # Dual console & file logger with rich prefixes
│   │
│   ├── network/                # Network & anti-bot resilience
│   │   └── fetcher.py          # TieredFetcher (curl_cffi + Playwright Stealth + HTTP Cache)
│   │
│   ├── collectors/             # Vendor Crawlers
│   │   ├── base.py             # BaseCollector abstract pipeline
│   │   ├── aula.py             # AULA collector
│   │   ├── atk_vxe.py          # ATK / VXE / VGN collector
│   │   ├── epomaker.py         # EPOMAKER collector
│   │   └── keychron.py         # Keychron collector
│   │
│   ├── artifacts/              # CAS & Safe Decompression
│   │   ├── cache.py            # Content-Addressed Storage (artifacts/ab/abcdef...)
│   │   ├── downloader.py       # Streaming downloader with SHA256 calculation
│   │   ├── extractor.py        # Safe unpacker (ZIP/7z/TAR) with zip-bomb protection
│   │   └── file_types.py       # Magic bytes & extension detector
│   │
│   ├── scanners/               # Static File Scanners (Zero execution)
│   │   ├── __init__.py         # Dispatcher for static scanners
│   │   ├── inf_scanner.py      # Windows Setup INF driver parser
│   │   ├── json_scanner.py     # JSON/JSON5 device definitions & VIA configs
│   │   ├── js_scanner.py       # WebHID / WebUSB filters & Electron JS bundles
│   │   ├── xml_scanner.py      # XML & AppxManifest parser
│   │   ├── sqlite_scanner.py   # Read-only embedded DB scanner
│   │   ├── text_scanner.py     # INI / TOML / YAML parser
│   │   └── binary_strings.py   # Static ASCII/UTF-16 strings scanner from PE files
│   │
│   ├── normalize/              # Data Cleansing & Deduplication
│   │   ├── models.py           # Canonical model names & category classification
│   │   ├── identifiers.py      # VID/PID parser, integer & 4-digit hex normalizer
│   │   ├── dedupe.py           # Conservative multi-factor deduplication
│   │   └── evidence.py         # EvidenceLevel definitions & fact structures
│   │
│   └── storage/                # SQLite Staging Registry
│       ├── database.py         # SQLite connection manager & query helpers
│       └── schema.py           # Database schema & indexes
│
├── data/
│   ├── registry.sqlite         # Primary SQLite staging database
│   └── reports/                # JSON and TXT reports after each crawl
├── artifacts/                  # Content-addressed artifact repository
├── extracted/                  # Sandboxed decompressed static files
├── logs/                       # Detailed run logs (logs/run-YYYY-MM-DD_HH-MM-SS.log)
└── tests/                      # Pytest automated test suite
```

---

## Adding a New Vendor Collector

To add a new vendor (e.g. `razer`, `logitech`, `wooting`):

1. Create a new collector in `ingest/collectors/<vendor>.py` inheriting from `BaseCollector`:
```python
from ingest.collectors.base import BaseCollector
from ingest.normalize.evidence import RawProduct

class RazerCollector(BaseCollector):
    @property
    def vendor_name(self) -> str:
        return "razer"

    @property
    def display_name(self) -> str:
        return "Razer"

    def collect(self, metadata_only: bool = False, no_download: bool = False):
        # 1. Fetch portal / web configurator
        # 2. Extract model info and driver links
        # 3. For each device: self.process_product(raw_product, no_download=no_download)
        pass
```
2. Register the collector in `ingest/collectors/__init__.py`:
```python
COLLECTORS_MAP["razer"] = RazerCollector
```

---

## Safe Recovery & Error Handling

- All network errors, unexpected HTML changes, or malformed archives are caught and logged with full tracebacks into `logs/run-*.log`.
- A failure in one vendor or product model will **never** interrupt the remaining crawling pipeline.
- Old database records are never hard-deleted; models missing from subsequent crawls are flagged as `active = 0`.
