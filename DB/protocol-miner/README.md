# Peripheral Protocol Miner

Stage B static-analysis tool for vendor utilities, drivers, web configurators,
firmware, and manifests. It creates evidence-backed protocol candidates for
human review; it never opens or writes to a real HID device.

## Current foundation

The first stage provides an isolated CLI, provenance records, shared-CAS
storage, reproducible run metadata, and versioned output schemas. It does not
alter the existing Registry Ingest database.

```powershell
cd protocol-miner
python miner.py doctor
python miner.py ingest .\inbox\vendor-utility.zip
```

`ingest` stores source bytes in the Registry Ingest CAS by default
(`../artifacts`) and writes only derived records below `workspace/`, `reports/`
and `candidates/`. Use `--cas-root` to point at another compatible CAS.

## Safety boundary

This project is static-only in its current stage. There is no HID transport,
no firmware flashing, and no executable launch path. Future dynamic adapters
must be explicit, sandboxed, and unable to access real HID devices.
