# Peripheral Protocol Miner

Stage B static-analysis tool for vendor utilities, drivers, web configurators,
firmware, and manifests. It creates evidence-backed protocol candidates for
human review; it never opens or writes to a real HID device.

## Current static pipeline

The tool is isolated from Registry Ingest and writes only derived data. The
implemented static stages cover provenance/shared CAS, type detection, bounded
ZIP/TAR/7z/nested/ASAR unpacking, JSON/INF/VIA/QMK/Vial extraction, WebHID and
WebUSB literals, source-map sources, bounded JavaScript buffer-builder
correlation, and PE import/transport reconnaissance. All resulting commands
are review candidates; no candidate is a production write.

```powershell
cd protocol-miner
python miner.py doctor
python miner.py ingest .\inbox\vendor-utility.zip
python miner.py ingest-cas sha256:<digest> --filename vendor-app.asar
python miner.py analyze <sha256>
python miner.py report <run-id>
```

`ingest` stores source bytes in the Registry Ingest CAS by default
(`../artifacts`) and writes only derived records below `workspace/`, `reports/`
and `candidates/`. Use `--cas-root` to point at another compatible CAS.

## Safety boundary

This project is static-only in its current stage. There is no HID transport,
no firmware flashing, and no executable launch path. Future dynamic adapters
must be explicit, sandboxed, and unable to access real HID devices.

`--allow-dynamic` and `--sandbox` are accepted for automation compatibility but
currently report that dynamic adapters are unavailable and continue static-only.
`--no-network` rejects `ingest-url`; `clean-workspace --yes` removes only
derived Miner directories and deliberately retains source CAS bytes.

## Review output

Each analysis creates a report directory containing artifact tree, identity,
topology, capabilities, commands, evidence, protocol candidate, contradictions,
unknowns, a future validation plan, run metadata, and a review-only registry
staging patch. The patch excludes unsafe command semantics.
