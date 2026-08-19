# Peripheral Protocol Miner

Stage B evidence-first protocol discovery tool for peripheral vendor utilities, drivers,
web configurators, firmware, and manifests. It creates evidence-backed protocol candidates
for human review; it never opens or writes raw bytes to a physical HID device.

## Architectural Pipeline

Protocol Miner implements an end-to-end evidence synthesis pipeline:

```text
Vendor Web App / Utility / Firmware / Manifest
        ↓
Static Extractors (AST, JSON, INF, VIA/QMK/Vial, PE, Descriptors)
        +
Fake-Device Dynamic Sandbox (Playwright Injected Fake navigator.hid / navigator.usb)
        ↓
Web UI Discovery & Dangerous Control Quarantine (DFU, Flash, EEPROM Clear blocked)
        ↓
Scripted Experiments & UI ↔ Transport Packet Sequence Correlation
        ↓
Transform Prediction & Unseen Validation Point Verification
        ↓
Adaptive Experiment Planner & Machine-Readable Research Needs (research_plan.json)
        ↓
Evidence Graph & Protocol Candidate Synthesis (ProtocolCandidate)
        ↓
.pevidence Tamper-Evident Bundles & Multi-Submission Corroboration
        ↓
Review-Only Registry Staging Patch (safe_for_production: false)
```

## Quick Start Commands

```powershell
cd DB/protocol-miner

# Tooling and sandbox readiness
python miner.py doctor
python miner.py browser-doctor

# Ingest and analyze artifacts
python miner.py ingest .\inbox\vendor-utility.zip
python miner.py ingest-cas sha256:<digest> --filename vendor-app.asar
python miner.py analyze <sha256>
python miner.py report <run-id>
python miner.py research-plan <run-id>

# Controlled WebHID configurator analysis
python miner.py analyze-web https://hub.vendor.com/webconfig --artifact-sha256 <sha256>

# .pevidence bundle workflows
python miner.py export-pevidence <run-id> --output ./candidates/device.pevidence
python miner.py validate-pevidence ./candidates/device.pevidence
python miner.py import-pevidence ./candidates/device.pevidence
```

## Safety Boundaries (ADR 0001 & ADR 0002)

1. **No Raw Physical Transport**: Protocol Miner contains no `send_raw_hid`, `hid_write`, or firmware flashing capabilities.
2. **Fake Browser Producer**: Chromium runs against mock `navigator.hid` / `navigator.usb` surfaces injected before vendor JS executes. No physical USB device is ever accessed.
3. **Vendor-Assisted Research Policy**: When observing real vendor software, only the official software communicates with hardware; the research controller acts strictly as a passive observer with mandatory baseline capture and rollback.
4. **Dangerous Control Quarantine**: Firmware flash, DFU bootloader, erase, and factory reset actions are forbidden and blocked from automated experiments.
5. **Privacy Scrubbing**: Bundles and logs automatically scrub user home paths, serial numbers, IP/MAC addresses, and personal keystrokes.
