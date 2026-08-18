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
| Bundle size | 2 472 348 bytes (this figure read 2 434 617 until 2026-08-18; the hash below is the identifier that matters, and it is unchanged) |
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
vendor's word for a magnetic switch.

**Correction from the second pass: the keyboard path uses none of them.** These
names belong to the mouse and dongle stacks. The keyboard SDK has its own
service API with its own numbering, where a command is a `(group, subcommand)`
pair rather than a symbolic name — see
[`aula-bytech-readdeviceuuid.md`](aula-bytech-readdeviceuuid.md). Nothing in the
list above is evidence about the keyboard.

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

The second pass generalised this rather than repeating it by hand: array
literals parsed, each rotation solved by evaluating candidate rotations against
the inline checksum, accessor offsets read off the accessor definitions, and
call sites rewritten against the nearest preceding alias binding. The bundle
carries 104 such arrays and 103 accessors; 93 rotation loops were located and
all 93 solve. The generalised pass reproduces the hand result above exactly,
which is what makes it trustworthy.

Recorded so the work is repeatable, not so it can be reused: no decoded vendor
source is stored in this repository.

## Protocol family: the vendor groups by SDK module, not by product id

The application stores a `sdkModuleName` per connected device and picks a
protocol module by it. The set of modules:

```text
sparkLinkV1   sparkLinkV2   bytech   bytechMechanical
hfd           hfdMechanical rlw      vision   hx   jm   ttgk
```

**Our board resolves to `bytech`.** Not inferred: the Info module that HERO 84 HE
loads (`info-CRdif018.js`, unobfuscated) sets `sdkModuleName = "bytech"` as a
constant of that class, and the store copies it on connect. Our board's
manufacturer string is `BY Tech`, which is consistent, but the module assignment
is the evidence, not the string.

This is the vendor's own notion of a protocol family, and it is a much better
family boundary than anything derivable from identity: it names the code that
speaks to the device. Candidate family id for the registry once a read is
verified: **`aula-bytech`** — scoped to what we have actually confirmed rather
than to every board the vendor files under `bytech`.

**The module name and the codec do coincide, and that was checked rather than
assumed.** There is exactly one keyboard codec class in the bundle, holding one
frame-config object, one command builder, one decoder and one response
validator; it is instantiated in exactly one place. Of the eleven
`sdkModuleName` values that have an Info module here, `bytech` is the only one
whose module imports that SDK — every other module binds a different one. So
frame format, command namespace, response validation and codec semantics are
shared across `bytech` by construction.

Two limits on what that buys. It shows the *driver* hands every `bytech` board
the same encoder, not that every such board's firmware answers the same way, so
the family stays `VendorArtifact` until an exchange happens. And
`bytechMechanical` is not a `bytech` sub-family on this evidence: it has no Info
module of its own, appears here as a UI panel name, and where the two are
grouped for behaviour it is grouped away from `bytech`. It is out of scope, not
included.

## How the vendor tells nine identical-looking models apart

It asks the device.

On connect, before anything else, the Info module calls `readDeviceUUID(...)`
and gets back a numeric device id. That value then selects the key layout:
`initDeviceLayout` switches on `deviceInfo.deviceId`, and elsewhere a lookup maps
ids to marketing names (for instance `19791209299978` → HERO 68 Air,
`19791209299984` → HERO 68 XS, `19791209299989` → AULA HERO 75 HE).

Two things follow:

- The UUID is a **model** identifier, not a per-unit serial. The application
  carries a static table of them, and they map to product names. Recording one
  is not a privacy problem the way a serial number is; it is still not recorded
  here, because none has been read from our board.
- This is the missing half of the "nine models share `0x372E:0x103E`" finding:
  the vendor distinguishes them with a protocol read. Any registry that wants to
  tell those nine apart has to do the same thing, which is precisely a
  protocol-evidence route to identity rather than a product-identity one.

## Keyboard service API

The keyboard SDK exposes, among others:

**Reads** — `readDeviceUUID`, `getUuid`, `getDeviceInfo`, `getFirmwareVersion`,
`getDeviceFeatures`, `getKeyMatrixPositions`, `getSupportedSwitches`,
`getSupportedAdvancedKeyTypes`, `getTravelPrecision`, `getPollingRate`,
`getDebounceTime`, `getSleepTime`, `getOsMode`, `getWinKeyLock`,
`getWasdArrowKeysSwapped`, `getComboOptimization`, `getLowPowerModeEnabled`,
`checkLowPowerModeSupported`, `getBatteryStatus`.

**KNOWN WRITE — NOT IMPLEMENTED**, recorded so they are recognisable and avoided:
`setPollingRate`, `setDebounceMode`, `setDebounceTime`, `setSleepTime`,
`setWinKeyLock`, `setWasdArrowKeysSwapped`, `setComboOptimization`,
`setLowPowerModeEnabled`, `resetKeyboard`, `resetUSB`, `resetDevice`, and the
whole OTA path (`encodeOTAStart`, `encodeOTAEnd`, `encodeCheckOTAStatus`,
`WIRELESS_OTA_MODE`).

None of these will be given a `SafeCommandId`, sent, or tested against hardware
by this ticket. `resetKeyboard`, `resetUSB` and the OTA calls are in the class
the project forbids outright.

`getBatteryStatus` is noted for the wireless question (spec Q3) and nothing more:
it belongs to a device we do not have a verified family for, and reading it is
still a protocol exchange.

It is also the clearest illustration of why "the SDK has a method" is not a
capability claim, and the second pass sharpened it: the Info module calls
`getBatteryStatus()` on the connect path for **every** `bytech` board including
ours, and then uses the result only for two of them — the two wireless models it
names explicitly. A method that exists, is on this model's call path, and whose
answer this model's own driver throws away. Five separate things are worth
tracking per method and they are not the same thing: the SDK has it; this model's
connect path calls it; its encoding is known; a hardware exchange has confirmed
it; the hardware actually has the capability.

## Frame format — mouse stack only

The bundle contains at least two protocol stacks. The one that was fully decoded
is the **mouse and dongle** stack, and its shape is recorded here explicitly
marked as *not* the keyboard's:

```text
payload length   63 bytes            (report id is prepended by sendReport -> 64 on the wire)
checksum         byte 0 = sum(bytes 1..62) & 0xFF
command ids      symbolic: UUID, BASICS, FIRMWARE_INFO, WIRELESS_ONLINE,
                 SLEEP_LEVEL, LOD, MOUSE_KEY, MACRO_DATA, OTA_CHECK, OTA_END
dispatch         responses matched by command id; unknown ids are logged as
                 "Unknown report response command ID:"
```

The 63-plus-report-id arithmetic is a useful corroboration of the 64-byte reports
TICKET-08 saw, but **it must not be assumed to hold for the keyboard**: it was
read out of the mouse protocol class, and this ticket is about a keyboard.

## What is still missing

**Nothing about `readDeviceUUID` — that one is recovered.** A second targeted
pass resolved the keyboard codec's own string array and read the command out of
it: group `0x82`, subcommand `0x01`, a 63-byte frame with a report-id-seeded
checksum, and a six-byte big-endian model id in the reply. The frame, the
checksum, the response validator and the decoder are written up in
[`aula-bytech-readdeviceuuid.md`](aula-bytech-readdeviceuuid.md), together with
the reason the exchange still has not happened: the ACL requires hardware
evidence for a `safe_read` and its schema cannot express a `(group, subcommand)`
pair, so there is no lawful route to send it yet.

The keyboard's frame does share the mouse stack's 63-byte payload size — but its
checksum is a different algorithm at a different offset, and the size was
established independently by our own report descriptor (`95 3F`, TICKET-08). The
correspondence is a coincidence of packet size, not shared framing, and the
warning above stands for every other field.

**Still missing for everything else.** The numeric ids for the rest of the
keyboard namespace are readable by the same method now, but nothing beyond
`readDeviceUUID` has been recovered deliberately: the vertical slice does not
need them, and an opcode written down is an opcode someone can be tempted to
send. Guessing any of them from ROYUAN, from QMK/VIA conventions, or from the
shape of `0xFF60:0x0061` remains out of bounds.
