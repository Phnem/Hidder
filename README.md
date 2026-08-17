# Peripheral

A local configurator for Hall-effect keyboards, and in time for mice. It
identifies the board itself, reads and writes actuation, rapid trigger, dead
zones and the rest, and works fully offline with no account.

Status: **skeleton**. Nothing talks to hardware yet. The crate boundaries, the
safety gate and the licence policy are in place first, on purpose: those are the
things that are expensive to add once there is code to retrofit them onto.

## Why it exists

Cheap Hall-effect keyboards ship with vendor drivers that are usually a web app,
often Windows-and-Chrome only, and tend to lose your profile on update. None of
them will configure a keyboard and a mouse from different brands together. The
layer above the Hall-effect features is already covered for the largest OEM
platform by an open project; the Hall-effect layer itself is not, and that is
where this starts.

## Repository layout

| Path | What lives there |
|---|---|
| `crates/ptransport` | HID transport. Owns every physical device handle; nothing above sees one |
| `crates/pcaps` | Capability vocabulary and the origin marker: how we know what we claim to know |
| `crates/pregistry` | Device registry and multi-signal fingerprinting with explicit confidence |
| `crates/psafety` | The only path from intent to a write: opcode ACL, rate limiting, kill switch |
| `crates/pproto` | Protocol engines, one trait per family, opcodes resolved per family |
| `crates/pprofile` | Profiles and cross-brand presets |
| `crates/plearn` | Learning mode for unknown hardware |
| `crates/pjournal` | What was written to a device, and where knowledge of a model came from |
| `crates/pcore` | Orchestration; the model the UI reads |
| `app/` | Desktop application (Tauri 2 + React) |
| `tools/emu` | Device emulator, so CI can run without hardware |
| `tools/protodoc` | Generates support and protocol docs from the registry |
| `tools/ingest` | Vendor-artifact ingestion. Separate workspace, never in a release build |
| `data/` | Reviewable device, layout and protocol sources |
| `docs/` | Decisions (ADRs) and prior-art notes |

Crate dependencies point one way only, and `scripts/check_crate_dag.py` enforces
it in CI rather than leaving it to review.

## Rules that are not negotiable here

These are constraints on the product, not preferences, and each one exists
because the failure it prevents happens to real hardware:

- **No firmware flashing.** Not in v1, under any circumstance.
- **Writes go through one gate.** In a release build the executor accepts only a
  generated `SafeCommandId`; a raw opcode from a data file cannot reach the
  transport without rebuilding the binary.
- **Unverified means read-only.** If the device's protocol family is not verified,
  the device opens read-only. There are no speculative write controls behind an
  "experiments" flag in the normal UI.
- **A device's answer is not proof, and its silence is not proof either.** An
  unsupported command often replays the previous reply; an unanswered opcode
  proves only that this firmware on this board does not implement it.
- **Never show a control with no confirmed command behind it.**
- **`tools/ingest` is physically outside the release build.**

## Building

Requires a Rust toolchain, Node 22 or newer, and the platform prerequisites Tauri
lists for your OS (on Windows: MSVC build tools and WebView2, the latter already
present on Windows 11).

```bash
cargo build --workspace
```

```bash
cd app && npm install && npx tauri dev
```

Checks, all of which run in CI on Windows, Linux and macOS:

```bash
cargo fmt --all --check && cargo clippy --workspace --all-targets -- -D warnings && cargo test --workspace
```

```bash
cargo deny --all-features check licenses bans sources
```

```bash
python3 scripts/check_crate_dag.py
```

## Licence

Proprietary; see [`LICENSE`](LICENSE) and
[ADR-0001](docs/decisions/0001-license.md). Dependencies are permissive-licensed
only, enforced by `cargo deny` in CI. Copyleft projects are read as a source of
protocol facts and engineering method, never as a source of code; what was learned
that way, and from where, is recorded in [`docs/prior-art/`](docs/prior-art/).
