# Initial architecture review — peripheral-configurator

Scope: validate the crate/layer structure proposed in the source plan (§3) against `codebase-design` principles, before ticket decomposition. Repository is greenfield — this is a review of a *proposal*, not of existing code, so findings are about seams to get right from the first commit rather than about fixing debt.

## 1. Which existing boundaries help or obstruct this task?

None exist yet. The proposed layering (UI → `pcore` orchestration → capability layer → protocol engines → transport) is sound as a *deep-module* stack: each layer hides substantially more than it exposes (transport hides OS HID APIs behind one enumerate/read/write surface; protocol engines hide opcode-level detail behind `ProtocolEngine`; capability layer hides vendor differences behind a flat `CapId` vocabulary). No objection to the proposed shape.

## 2. Is prefactoring required before feature implementation?

No — nothing to refactor. The risk is the opposite: over-building crate boundaries before any protocol engine exists to validate them. Phase 1 tickets should stand up the minimum crate set needed for one read-only vertical slice (AULA), not all nine crates fully fleshed out.

## 3. Which modules should remain stable once introduced?

- `pcaps` (`CapId` vocabulary + value types + `origin` marker) — every other layer depends on this. Changing a `CapId`'s type/unit after protocol engines are written against it is expensive. Get the AULA-relevant subset (`he.actuation`, `he.rt.*`, `he.deadzone.*`) right before writing engine code against it.
- `psafety`'s `SafeCommandId` boundary — this is the safety-critical seam from the spec (FR2). Once `pcore`/UI code is written against "execute only accepts `SafeCommandId`", that invariant must never be weakened by a later convenience overload that accepts raw opcodes.
- `ProtocolEngine` trait signature — expect it to be *nearly* stable after the AULA engine is written, but do not consider it final until a second, structurally different family has been implemented against it (Phase 4 / TICKET-17). Which family that will be is decided by evidence, not named in advance (updated 2026-08-17 — the ROYUAN purchase was cancelled and the EPOMAKER moved to the remote-validation track). One family cannot validate a trait's generality.

## 4. Where should complexity be hidden?

- HID transport — entirely inside `ptransport`, built on `hidapi` as the primary cross-platform abstraction (Windows: `hid.dll`; Linux: `hidraw` backend; macOS: `IOHIDManager` backend), with direct platform calls (`HidD_*`, native `IOHIDManager`) as an escape hatch used only when `hidapi` doesn't expose a needed capability — still confined to `ptransport`. No `#[cfg(windows)]` should leak above this crate.
- **Physical device ownership** — entirely inside `ptransport`, one level deeper than "which OS API": each device is owned by exactly one `DeviceSession`, which holds the actual handle (`hidapi::HidDevice` or an escape-hatch platform type) and runs blocking HID I/O on a dedicated worker. `hidapi::HidDevice` is `Send` but not `Sync`, and reads can block, so no code outside that one worker may touch the handle — this is a stronger requirement than merely hiding *which* OS API is in use (see revised finding in §6).
- Multi-signal fingerprint scoring (§6.2 of the plan) — entirely inside `pregistry`. Callers ask "what device is this, what confidence" and get back one answer, not raw signal vectors.
- Anti-fiction filtering, rate limiting, ACL enforcement — entirely inside `psafety`. `pcore` and the UI must not be able to bypass it by calling a lower layer directly; there must be exactly one path from UI intent to a device write, and it runs through `psafety`.
- Ordering guarantees for high-frequency streams (`he.analog_stream`, Learning Mode capture) — entirely inside the IPC layer between `pcore` and the UI, via Tauri Channels, not the general event bus. Callers on the UI side receive an already-ordered stream; they must not need to re-sequence events themselves.

## 5. Which public interfaces may need to change as evidence comes in?

- `ProtocolEngine::verify` — the spec (FR1) mandates it, but the *strategy* per capability (readback vs indirect readback vs analog-stream vs user-confirm) will only become concrete once real I/O happens (TICKET-12/15 on AULA; TICKET-09 contributes descriptor-level evidence remotely, not I/O). Expect the `Verification` return type to grow variants; do not hard-code a binary pass/fail now.
- `CapValue` — needs to accommodate scalar, struct (DKS, SOCD), matrix (keymap), and stream (analog) shapes from day one (see Приложение A in `spec.md`); retrofitting stream support onto a scalar-only enum later would be a breaking change across every engine.

## 6. Architectural improvements required now (before/during Phase 0–1 tickets)

- **REQUIRED_BEFORE_IMPLEMENTATION** — `ptransport` must expose devices as a `DeviceSession`/`SessionHandle` (by `DeviceId`), not a bare opaque handle that any caller can hold. Each session owns its physical `hidapi::HidDevice` (or escape-hatch platform handle) exclusively, runs blocking HID I/O on one dedicated worker, and is reached only through an async command queue. No `HidDevice`/`HANDLE`/`fd`/`IOHIDDeviceRef` may cross the crate boundary, and no code outside that one worker may call read/write/get_feature_report on it. This is stronger than "just hide the OS type" — it also forecloses the mutex-around-one-handle failure mode that shows up once multiple upper-layer calls (a read poll, a write, a stream subscription) target the same device concurrently. Must be decided *before* TICKET-08 (the Windows HID inventory experiment), because exploratory code written directly against `HidD_*`/`hidapi` will otherwise leak handle ownership assumptions into whatever calls it.
- **REQUIRED_BEFORE_IMPLEMENTATION** — `tools/ingest` must be excluded from the default workspace build (separate `Cargo.toml`, not a `default-members` entry) from the very first workspace skeleton commit (TICKET-07). This is now both a reputational *and* a licensing requirement (ADR-0001): nothing that touches decompiled vendor artifacts may risk being pulled into what gets shipped.
- **REQUIRED_BEFORE_IMPLEMENTATION** — the `pcore` ↔ UI IPC surface must be split into three Tauri mechanisms from the skeleton (TICKET-13), not introduced ad hoc later: **commands** for request/response, **events** for low-frequency notifications, **channels** for ordered high-throughput streams. Getting this shape right in the skeleton matters because retrofitting Channels onto code already wired through the general event bus (once Analog Monitor exists in the Phase-2 epic) is a breaking change to the IPC contract, not an addition.
- **REQUIRED_DURING_IMPLEMENTATION** — `SafeCommandId` build-time codegen (reading `opcode_acl` classification and emitting a closed enum) needs an owner crate/build-script decision. Does not block the workspace skeleton or the read-only AULA engine (which only needs `safe_read`), but must be settled before any ticket introduces a write path (Phase 2, out of this ticket set's range) — flag as a dependency on the Phase-2 epic ticket (issue 15), not on Phase 1.

## 7. Improvements that are merely desirable follow-up

- **FOLLOW_UP** — capability-graceful-degradation logic (applying a preset against a partial capability set) belongs in `pprofile`, not duplicated per engine. Relevant when `pprofile` is designed (Phase 2/4 epic), not now.
- **FOLLOW_UP** — separating the device-data build output into a redistributable open subset (tied to the open Q1 in `spec.md`, device-data licensing) has a build-pipeline implication for `pregistry` (need two build targets: full registry for the app, open subset for community redistribution) — worth a design note when Phase 3/4 tickets are split, not before.

## 8. Not relevant to current scope

- **NOT_RELEVANT_TO_SCOPE** — `mouse.*` capability namespace and `MouseProtocol`/`ReceiverMouse` design (Phase 6). The plan already reserves the namespace in `pcaps`; no crate work needed until Phase 6 tickets are split.
- **NOT_RELEVANT_TO_SCOPE** — `dev.power.*`/`dev.connection` capability wiring to a real protocol engine (spec FR12, TICKET-20). Both Phase 0/1 reference boards are USB-only; the capability exists in `pcaps` from the day it's added, but no engine can populate it until a wireless-capable family exists (Phase 6 track). The tray *shell* (icon, quick panel, click behavior) is not blocked by this — see finding below.

**Added note (2026-08-17):** tray-icon rendering (percent → 16/20/32px RGBA buffer) is presentation logic and belongs in `app`, not in any `p*` crate — `pcaps`/`pcore` expose `BatteryState { percent, charging }` and nothing about pixels. This keeps the same boundary discipline as §4 (complexity hidden behind the layer that owns it) and avoids a rasterization dependency creeping into the core crate graph.

## 9. Crate dependency structure

Each crate has a single responsibility, and crate dependencies form a **one-way DAG** — lower layers never depend on upper layers, and no cycles are permitted. This is stronger than "each crate doesn't know about the others" (crates plainly must depend on each other for `pcore` to orchestrate anything) — the actual rule is about direction, not ignorance:

```text
                app
                 │
               pcore
         ┌───────┼────────┐
         ▼       ▼        ▼
     pprofile   plearn   pjournal
         │       │
         └───┬───┘
             ▼
           pproto
         ┌───┼────┐
         ▼   ▼    ▼
       pcaps pregistry psafety
             │
             ▼
         ptransport
```

The exact shape may shift once TICKET-10–12 write real code against it, but the constraint that ticket reviews must enforce is the direction, not this specific diagram: `ptransport` must never import from `pproto`/`pcore`; `pcaps`/`pregistry`/`psafety` must never import from `pprofile`/`plearn`/`pjournal`; nothing below `pcore` may import `app`. **REQUIRED_BEFORE_IMPLEMENTATION** for TICKET-07 — get `Cargo.toml` path-dependency direction right in the skeleton, since Cargo will happily let a workspace grow a cycle if no one is watching, and detangling one later touches every crate it's implicated in.

## Risks for ticket review to watch

1. **Type or handle leakage across `ptransport`.** Any PR that imports a Win32/IOKit/hidraw/`hidapi::HidDevice` type outside `ptransport`, or that lets code outside a device's own worker touch its handle, is a regression, not a style nit — it defeats the reason the crate exists and reopens the mutex-around-one-handle failure mode §6 rules out.
2. **Silent write-path bypass.** Any code path that calls into a protocol engine's write logic without going through `psafety`'s `SafeCommandId` gate is a `BLOCKING` review finding, not `IMPORTANT_FOLLOW_UP` — this is the one invariant the whole safety story (spec FR2/FR3, plan §10.6) depends on.
3. **Premature trait finalization.** Locking `ProtocolEngine` or `CapValue` down as "stable" after only the AULA engine exists — there is exactly one data point; treat the trait as provisional until a second family lands. **This risk grew on 2026-08-17**, and must stay visible rather than be quietly treated as mitigated: there is no second board on the developer's desk at all (the ROYUAN purchase was cancelled; the EPOMAKER lives with a third party and is now a remote-validation device reached only through a shipped build). Until then the only second "device" available to tests is the emulator, which was built from AULA's own recordings and therefore cannot falsify AULA-shaped assumptions.
4. **High-frequency data routed through Tauri events instead of Channels.** Any analog-stream or capture-stream code that subscribes via the general event listener API instead of a Channel is a `BLOCKING` finding once Analog Monitor exists (Phase-2 epic) — ordering is not incidental for a per-key analog waveform.
5. **Reversed or cyclic crate dependency.** A `Cargo.toml` path dependency that points from a lower layer (§9 diagram) to a higher one is a `BLOCKING` finding, not a style preference — it silently reintroduces the coupling the crate split was meant to prevent.
