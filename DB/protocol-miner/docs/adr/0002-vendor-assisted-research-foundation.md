# ADR 0002: Vendor-Assisted Research Foundation & Safety Boundary

## Status

Accepted (2026-08-19).

## Context

ADR 0001 established that Protocol Miner (Stage B) contains no raw HID write APIs (`send_raw_hid`, `hid_write`, etc.) and performs static analysis with safe simulated dynamic trace ingestion.

To support deeper research against hardware when static or fake-sandbox data is insufficient, a mechanism is needed to observe actual device responses. However, introducing arbitrary HID write capabilities inside the miner or research agents would compromise the evidence-first architecture and bypass production safety controls (`psafety`).

## Decision

We establish the **Vendor-Assisted Research Foundation**:

1. **Strict Separation of Writers and Observers**:
   - **Protocol Miner / Research Controller**: Acts solely as an external orchestrator and passive transport observer. It NEVER constructs or sends raw, arbitrary HID packets to hardware.
   - **Official Vendor Software / Web Hub**: Remains the ONLY component that transmits write packets to the physical device.

2. **No `psafety` Bypass**:
   - Because the research controller only drives official vendor UI controls (via browser automation or controlled inputs) and observes the resulting traffic, it cannot construct arbitrary or out-of-spec HID writes.
   - The production write path remains strictly:
     $$\text{SafeCommandId} \longrightarrow \text{psafety} \longrightarrow \text{typed protocol command} \longrightarrow \text{ptransport}$$
   - Protocol Miner outputs candidates with `safe_for_production: false`.

3. **Mandatory Baseline & Rollback Policy**:
   - Before executing any reversible parameter experiment on a real device, the controller records the current baseline value and device topology.
   - Every experiment step is followed by an automated rollback action through the vendor UI.
   - At the conclusion of a session, a full baseline restore is executed. If restore cannot be confirmed, the session status is marked `RESTORE_UNCERTAIN` and further automation halts immediately.

4. **Forbidden Dangerous Actions**:
   - The controller enforces hard quarantines against dangerous operations: Firmware Updates, Flashing, Bootloader/DFU entry, Factory Reset, Pairing Reset, EEPROM Clear, and Recovery Mode.
   - High-risk / unmodeled features (SOCD, Calibration, DKS, Macro execution, Fn remap) remain `REVIEW_ONLY` and are skipped during automated runs.

5. **Versioned `.pevidence` Bundles**:
   - All observations, traces, actions, and restore logs are packaged into tamper-evident, versioned `.pevidence` bundles with SHA-256 integrity maps and privacy scrubbing.

## Consequences

- The safety boundary from ADR 0001 is preserved.
- Hardware observation produces high-confidence evidence without risk of bricking devices.
- All community contributions and research sessions remain traceable, reproducible, and verifiable.
