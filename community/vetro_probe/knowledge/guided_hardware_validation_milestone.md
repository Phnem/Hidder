# Vetro Guided Hardware Validation: Real-Hardware E2E Milestone

**Date**: 2026-08-29  
**Scope**: AULA HERO84 HE (`0x372E:0x103E`), FW `0216`, Wired USB  
**Engine**: Unified Guided Validation Engine v0.3.1  
**Status**: **COMPLETE_PASS** (REAL_HARDWARE VERIFIED)

---

## 1. Verified Architecture Components

The following subsystems have successfully executed and passed on physical gaming keyboard hardware:

1. **Guided Validation Core Engine (`GuidedValidationEngine`)**:
   - Status: **REAL_HARDWARE VERIFIED**
   - Protocol/observable orchestration across machine-readable validation plan.
2. **Lighting Brightness Guided Validator (`LightingCapabilityValidator`)**:
   - Status: **REAL_HARDWARE VERIFIED** (Scope: `0x372E:0x103E` / `aula_kb_v3_wired` / FW `0216`)
   - Validated discrete brightness shift (10 -> 20) with human visual observable confirmation.
3. **Exact Identity Gate (`ExactIdentityGate`)**:
   - Status: **REAL_HARDWARE VERIFIED**
   - Verified physical VID/PID, descriptor hash (`62b16777e3455bb0`), firmware branch (`0216`), and wired connection mode against reviewed preview bundle.
4. **Rollback & Final-State Verification Pipeline**:
   - Status: **REAL_HARDWARE VERIFIED**
   - Captured live baseline, applied safe mutation, confirmed semantic readback, restored captured baseline, verified post-rollback identity with zero residual state drift.
5. **Capability-Scoped Device Certificate Pipeline (`DeviceValidationCertificate`)**:
   - Status: **REAL_HARDWARE VERIFIED**
   - Minted `cert-efd256a44797` under schema `vetro.hardware-validation-certificate.v2`.

---

## 2. Capabilities Explicitly NOT Hardware-Verified in this Milestone

The following operations were NOT part of this physical campaign and remain blocked from runtime promotion:

- `he.actuation`: **BLOCKED_PENDING_INDEPENDENT_THRESHOLD_OBSERVABLE** (requires dedicated comparative threshold observable).
- `he.deadzone`: **BLOCKED_PENDING_ANALOG_TELEMETRY_STREAM**.
- `he.rt` (Rapid Trigger): **BLOCKED_BY_KNOWLEDGE_HOLE** (`rapid_trigger_units_crosscheck` OPEN).
- `keyboard.remap`: **BLOCKED_BY_MISSING_STRONG_E5** (requires device-correlated `WM_INPUT` raw input hook).
- `light.global_color`: **BLOCKED_BY_UNRESOLVED_LIGHTING_FEATURE**.
- `light.effect` / `light.speed` / `custom.per_key`: **BLOCKED_BY_UNRESOLVED_LIGHTING_FEATURE**.

---

## 3. Exact Physical Proof Chain

```text
ExactIdentityGate (bundle: aula-hero-84-he)
    ↓ [MATCH] PhysicalInstance(vid=0x372E, pid=0x103E, fw=0216, mode=wired, hash=62b16777e3455bb0)
Reviewed GuidedValidationPlan (vetro.guided-validation-plan.v1)
    ↓ [PLAN] Eligible: lighting.brightness; Excluded: he.actuation, remap, global_color
Live Baseline GET
    ↓ [GET] light.brightness -> 10 (50%)
Typed Temporary SET
    ↓ [SET] light.brightness -> 20 (100%)
Protocol Semantic Readback
    ↓ [GET] light.brightness -> 20 (matched temporary value)
Human Physical Observable
    ↓ [PROMPT] "Did the keyboard lighting visibly change to the test mode?" -> "y" (latency: 4790ms)
Typed Rollback to Captured Baseline
    ↓ [SET] light.brightness -> 10
Post-Rollback Verification GET
    ↓ [GET] light.brightness -> 10 == baseline (matched original state)
Final-State Integrity Verification
    ↓ [VERIFIED] final_state_verified: true
DeviceValidationCertificate Minting
    ↓ [MINT] cert-efd256a44797 (validated_capability_groups: ["lighting.brightness"])
Certificate Persistent Store
    ↓ [SAVED] vetro_guided_physical_run/certificates/cert_372E_103E_0216.json
```

---

## 4. Run Provenance Metadata

- **Certificate ID**: `cert-efd256a44797`
- **Schema**: `vetro.hardware-validation-certificate.v2`
- **App Version**: `0.3.1`
- **Engine Version**: `0.3.1`
- **Build Commit**: `fbf95cbdb34a`
- **Knowledge Revision**: `ca1d15723479`
- **Device**: AULA HERO84 HE (`0x372E:0x103E`)
- **Firmware Branch**: `0216`
- **Connection Mode**: `wired`
- **Terminal Verdict**: `COMPLETE_PASS`
- **Validated Capability Groups**: `["lighting.brightness"]`
- **Duration**: `4.83s`
- **Frozen Fixture Reference**: `community/vetro_probe/fixtures/real_runs/aula_hero84_fw0216_guided_cert.json`
