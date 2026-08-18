# AULA HUB web driver — protocol facts

Facts extracted from the official AULA WebHID configurator, for TICKET-12. Facts
only: no vendor code is reproduced here and none is copied into Peripheral. The
Rust implementation is written independently from what is written down below
(ADR-0001, mode `facts`).

**Nothing in this document has been confirmed against our hardware.** Everything
here is `origin: FromVendorJs` until an exchange happens. No command has been
sent to the keyboard at any point while producing it.

## Provenance

| Field | Value |
|---|---|
| Configurator | AULA HUB, the vendor's browser-based driver |
| Landing page | `https://aulastar.com/aula-hub/` (links to the app) |
| Application | `https://hub.aulacn.com/` |
| Main bundle | `https://hub.aulacn.com/assets/index-BYKWhEoJ.js` |
| Bundle size | 2 434 617 bytes |
| Bundle SHA-256 | `9c6c6a71796243073b6512d3e3aa1946ee76b77182c57edf5722149b3a37d0ab` |
| Retrieved | 2026-08-18T05:39:35Z |
| Source map | **none published** — `index-BYKWhEoJ.js.map` returns the SPA HTML fallback, not a map |
| Lazy chunks | 122, loaded per device page |

The vendor does not version the app visibly; the bundle hash is the only version
identifier, which is why it is recorded. A different hash means these facts must
be rechecked before they are relied on.

## Our board in the vendor's device table

```text
pName        "HERO 84 HE"
vId          14126   0x372E
pId          4158    0x103E
usage        97      0x61
usagePage    65376   0xFF60
supportAcc   1
wireless     0
deviceType   1
```

Two conclusions, both load-bearing:

**The config channel is `0xFF60:0x0061`.** The vendor's own `requestDevice`
filter for this board is exactly that usage/usagePage pair, which is the vendor
collection TICKET-08 found on the physical board. The other vendor collection,
`0xFF00:0x0001`, is not the configuration channel — it is not what the vendor
opens, and it is excluded by construction from the report-id rule below because
it carries feature reports only.

**Nine different models share `0x372E:0x103E`.** From the same table: HERO 68 HE,
HERO 68 MINI, WIN68HE Ultra, HERO 84 HE, HERO 99 HE, HERO 68 Air, HERO 68 XS,
AULA HERO 75 HE, HERO68 MINI Air. The vendor cannot tell them apart by VID:PID
either — the user picks the model in the interface. This is the domain rule
"a product id is an index, not an answer" confirmed on our own hardware by the
manufacturer's own software, and it is why the registry treats a VID:PID match
with a disagreeing product string as `Candidate` rather than a match.

## Transport

WebHID, and the shape of it is ordinary:

| Fact | Value | Note |
|---|---|---|
| Direction out | `device.sendReport(reportId, payload)` | output reports, not feature reports |
| Direction in | `inputreport` events | one handler, dispatched through a queue |
| Report ID | **derived at runtime, not hardcoded** | see below |
| Timeout | 500 ms per command | vendor default |
| Retry | 3 | vendor default |
| Feature reports | one `sendFeatureReport` call in the whole bundle, no `receiveFeatureReport` | the config channel is not a feature-report channel |

**How the report id is chosen.** The vendor scans the opened device's
collections for one that has *both* input reports and output reports, then takes
`outputReports[0].reportId`. If none exists it throws rather than guessing.

On our board that selects the `0xFF60:0x0061` collection and yields **report id
9**, matching TICKET-08 exactly. Worth copying as a design idea rather than as
a constant: deriving the report id from the descriptor is more robust than
hardcoding 9, and it is what the vendor does.

The transport also keeps a command queue, a busy flag, and a `responseValidator`
hook, which tells us responses are matched to requests rather than assumed to
arrive in order.

## Command names

The bundle carries a set of command identifiers. Numeric opcodes are **not yet
established** — the mapping from these names to bytes has not been recovered, and
nothing below should be treated as an opcode:

```text
DevGetInfo            DevGetAxisCfg         DevSetAxisCfg
DevGetShortcutKey     DevSetShortcutKey     DevGetButtonInfo
DevSetButtonInfo      DevGetMouseCfg        DevSetMouseCfg
DevGetMouseDpi        DevSetMouseDpi        DevGetSensorAngle
DevSetSensorAngle     DevGetDongleLEDEfficacy  DevSetDongleLEDEfficacy
DevGetPairResult      DevSetMacro
```

Most of this set is mouse and dongle vocabulary; `DevGetInfo` and
`DevGetAxisCfg` are the two that plausibly apply to a keyboard, "axis" being the
vendor's word for a magnetic switch. Which of these the keyboard path actually
uses is not established.

## Pages the keyboard loads

`HERO 84 HE` maps to these lazy modules: `info`, `gifLight`, `light`, `trigger`,
`key`, `high`, `perf`, `version`.

The `version` module is **not** a "read the firmware version" path: it is the OTA
update page — release notes PDF, download URL, progress, and a `resetDevice()`
call. It reads the firmware version out of an application store that something
else populated, so the version read happens on the connect path rather than
there.

`resetDevice()` is recorded here as a **known write, deliberately not
implemented**, and it is exactly the kind of call that must never acquire a
`SafeCommandId`.

## Deobfuscation notes

The bundle uses string-array obfuscation: a literal array of strings, rotated at
load until an arithmetic checksum over selected entries matches a constant, and
an accessor that indexes it with a fixed offset. Recovering it needs no
execution of application code — the array is a literal, the rotation is
`push(shift())`, and the checksum expression is inline.

For the transport module the accessor resolves as `index - 240` into a 169-entry
array after 160 rotations. Confirmed by decoding known-good positions:
`device`, `reportId`, `send`, `sendCommand`, `onInputReport`, `resetQueue`.

Recorded so the work is repeatable, not so it can be reused: no decoded vendor
source is stored in this repository.

## What is still missing

The numeric opcode for any command, including the one this ticket wants: a
single, provably read-only request issued on the connect path. Until that is
recovered from the artifact, TICKET-12 has nothing it is allowed to send, and
guessing one from ROYUAN, from QMK/VIA conventions, or from the shape of
`0xFF60:0x0061` is explicitly out of bounds. The usage page confirms which
channel the vendor talks on; it does not tell us what it says.
