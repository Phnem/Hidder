# `readDeviceUUID` — pre-flight for the first AULA exchange

> **Status: the exchange happened on 2026-08-18 and every prediction below held.**
> The result, and what it does and does not establish, is in
> [`docs/hardware/aula-bytech-exchange-001.md`](../hardware/aula-bytech-exchange-001.md).
> This document is left as written, before the send, so the predictions can still
> be read as predictions.

Targeted deobfuscation pass for TICKET-12. This document recovers one command
end to end and stops there. **Nothing in it has been sent to any device.** No
byte has left this machine towards the keyboard, and none will until the plan in
§20 is separately approved.

Facts only, written independently of vendor code (ADR-0001, mode `facts`). No
vendor source is reproduced or vendored here.

## Provenance of this pass

| Field | Value |
|---|---|
| Artifact | `https://hub.aulacn.com/assets/index-BYKWhEoJ.js` |
| SHA-256 | `9c6c6a71796243073b6512d3e3aa1946ee76b77182c57edf5722149b3a37d0ab` |
| Re-retrieved | 2026-08-18 |
| Chunks read | `info-CRdif018.js` (HERO 84 HE Info module) and the ten other `info-*.js` modules, for the family test in §14 |

The hash is byte-identical to the one recorded in `aula-webhid.md`, so this pass
studies the same artifact as the earlier one. (The byte count recorded there,
2 434 617, is wrong; the artifact is 2 472 348 bytes. The hash is the identifier
that matters and it matches.)

Method: the string-array obfuscation was resolved statically — array literals
parsed, the `push(shift())` rotation solved by evaluating each candidate
rotation against the inline arithmetic checksum, accessor offsets read off the
accessor definitions, and call sites rewritten against the nearest preceding
alias binding. **No application code was executed.** The pass reproduces the
earlier result exactly where they overlap (the transport module's array: 169
entries, offset 240, 160 rotations), which is the check that the method is
sound.

---

## 1. Vendor command name

`readDeviceUUID` — a static entry point on the keyboard SDK class. It constructs
a short-lived device instance, calls `getUuid()` on the base service, and tears
the instance down in a `finally`. `getUuid()` is the method that encodes.

## 2. Numeric command id

| Field | Value |
|---|---|
| Command group | **130** = `0x82` |
| Subcommand | **1** = `0x01` |
| Payload | six zero bytes |

The pair `(130, 1)` occurs **exactly once** in the whole bundle. There is no
second command that encodes to it.

Group structure, from every `encode(...)` call site in the bundle:

- `0x82` (130) — read group, subcommands 0–15. Includes uuid (1), firmware
  version (2), supported switches (3), advanced-key types (4), travel precision
  (8).
- `0x84` (132) — read group, subcommands 17–33 (polling rate, win-key lock,
  debounce, sleep, combo optimisation…).
- `0x04` (4) — the **write** counterpart of `0x84`: the identical subcommand
  numbers, `0x84 & 0x7F == 0x04`.

By the same arithmetic the write counterpart of `0x82` would be `0x02`. **`0x02`
does not appear as a command group anywhere in the bundle.** Group `0x82` is
read-only in this SDK, which is part of the argument in §10.

## 3. Config endpoint

`0xFF60:0x0061`, the vendor collection TICKET-08 found on our board. The SDK's
device filter is `{vendorId: 14126, usage: 97, usagePage: 65376, productId: …}`
— `0x372E`, `0x61`, `0xFF60`. Unchanged from the earlier pass, now read off the
filter constant rather than off a `requestDevice` call.

## 4. Report id: derived, and why it is 9 here

The SDK does not hardcode a report id. It scans the opened device's collections
for the first one that has **both** input reports and output reports, takes
`outputReports[0].reportId`, and throws `No output report ID found` rather than
guessing.

On our board that resolves against our own captured descriptor for
`0xFF60:0x0061` (TICKET-08, `docs/hardware/aula-hero-84-he.md`):

```text
06 60 FF   Usage Page (Vendor 0xFF60)
09 61      Usage (0x61)
A1 01      Collection (Application)
85 09        Report ID (9)
09 62 ... 95 3F 81 02    Input,  count 0x3F = 63 bytes
09 63 ... 95 3F 91 02    Output, count 0x3F = 63 bytes
C0         End Collection
```

One collection, both directions, report id **9**, 63 data bytes each way. The
"in 64 / out 64" in the inventory is 63 + the report-id byte.

This is worth stating precisely because it is the one place where our own
hardware evidence and the vendor artifact meet: the frame size 63 is asserted by
the vendor's frame config *and* independently by our board's own report
descriptor. They agree.

We should derive the report id the same way rather than hardcode 9. 9 is what
the derivation yields on this board, not an input to it.

## 5. The request frame, byte by byte

The frame builder writes a fixed 63-byte packet:

| Index | Meaning | Value here |
|---|---|---|
| 0 | command group | `0x82` |
| 1 | subcommand | `0x01` |
| 2 | reserved, written as zero | `0x00` |
| 3 | total packet count | `0x01` |
| 4 | current packet index (0-based) | `0x00` |
| 5 | data length in this packet | `0x06` |
| 6 … 61 | data, zero-padded to the end | six `0x00`, then padding |
| 62 | checksum | `0x6C` |

Chunking: `ceil(len(data) / 56)`, minimum 1. Usable data per packet is
`63 - 6 (header) - 1 (tail) = 56`. Six bytes is one packet.

Full frame, report id 9:

```text
82 01 00 01 00 06 00 00 00 00 00 00 00 00 00 00
00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
00 00 00 00 00 00 00 00 00 00 00 00 00 00 6C
```

63 bytes. The report id is prepended by the HID write, giving 64 on the wire.

**The six zero data bytes are not padding.** They are an explicit argument the
vendor passes, and they set the length byte to 6. Sending length 0 would be a
different request. This mirrors `getFirmwareVersion`, which passes two zero
bytes and gets two back: the request states how many bytes it expects.

## 6. Checksum

Stated without the circularity the first draft had — `frame[62]` is the
checksum, so summing "the whole frame" needs saying which value the slot holds
while the sum runs. The builder zeroes it first:

```text
frame[62] = 0
checksum  = 0xFF - ((report_id + Σ frame[0..=61]) & 0xFF)
frame[62] = checksum
```

Summing `0..=61` and summing `0..=62` with the slot at zero are the same number;
the first spelling is the one with no order-of-operations trap in it, and it is
how our implementation is written. The finished frame satisfies one invariant,
which is the cheapest way to state the whole thing and the form to test against:

```text
(report_id + Σ frame[0..=62]) & 0xFF == 0xFF
```

Note the two properties that make this *not* the mouse stack's checksum:

- it is a **two's-complement-style negation** (`255 - sum % 256`), not a plain
  truncated sum;
- **the report id is part of the sum**, as a seed, even though it is not part of
  the 63 bytes. A frame is only valid for the report id it was built for.

For our frame: `9 + 0x82 + 0x01 + 0x01 + 0x06 = 9 + 138 = 147`, and
`255 - 147 = 108 = 0x6C`.

The mouse stack's `byte0 = sum(bytes 1..62) & 0xFF` recorded in `aula-webhid.md`
is a different algorithm at a different offset. It was not used, assumed, or
consulted here. This checksum comes from the keyboard codec.

## 7. Expected response structure

The response arrives as an input report on the same collection, 63 data bytes.
**The report id is not part of it** on WebHID; on a Windows HID read it will be
byte 0 and must be stripped before these offsets apply.

| Index | Meaning | Expected |
|---|---|---|
| 0 | command group, echoed | `0x82` |
| 1 | subcommand, echoed | `0x01` |
| 2 | — | not checked by the vendor |
| 3 | total packet count, echoed | `0x01` |
| 4 | current packet index, echoed | `0x00` |
| 5 | data length | expected `0x06` |
| 6 … 6+len | data — the model id, big-endian | six bytes |
| 62 | checksum | present, **not verified by the vendor** |

## 8. Response validator / discriminator

The transport matches a report against the head of the command queue only, and
drops anything that does not match. Before that it checks for unsolicited
"bubble" reports and routes those away as events.

Bubble command ids: `254`, `152`, `148`. Also a dongle report shape: length 19
with byte 0 == 10. `0x82` is in neither set, so a UUID reply is never mistaken
for an event, and no event is ever mistaken for a UUID reply.

The validator, in full:

```text
response[0] == request[0]   (command group)
response[1] == request[1]   (subcommand)
response[3] == request[3]   (total packet count)
response[4] == request[4]   (current packet index)
```

That is the whole discriminator. There is **no sequence number and no status
byte** in this protocol, and the checksum is not checked on the way in.

Consequences we should not inherit uncritically:

- Four echoed header bytes is a weak matcher when only one command is in flight
  at a time, which is the vendor's arrangement (single queue, one outstanding
  command). It is a bad matcher for anything concurrent. Our engine should keep
  the same one-at-a-time discipline rather than strengthen the matcher.
- We *should* additionally verify the response checksum, since we can compute
  it. But it must be **logged and compared, not enforced**, on the first
  exchange: the vendor never checks it, so whether the device even computes it
  the same way (same negation, same report-id seed) is unverified. Enforcing an
  unverified rule would turn a good read into a spurious failure.
- We should require `response[5] == 6` and reject `response[5] > 56`.

## 9. Decode semantics

```text
payload = response[6 .. 6 + response[5]]      (clamped to 6 + 56)
value   = big-endian unsigned integer over payload
```

Big-endian: the first payload byte is the most significant. The vendor
accumulates in `BigInt` and then narrows to a JS `Number`, which is lossless
here (the values are 45-bit) but is a lossy step in general.

The ten model ids the HERO 84 HE Info module carries decode as exactly six bytes
each, and the structure is unmistakable:

| Decimal | Hex | Bytes | Vendor name |
|---|---|---|---|
| 18691697672195 | `0x110000000003` | `11 00 00 00 00 03` | — |
| 18691697672197 | `0x110000000005` | `11 00 00 00 00 05` | — (has its own layout) |
| 18691697672207 | `0x11000000000F` | `11 00 00 00 00 0F` | — |
| 18691697672210 | `0x110000000012` | `11 00 00 00 00 12` | — (has its own layout) |
| 18691697672213 | `0x110000000015` | `11 00 00 00 00 15` | — |
| 18691697672255 | `0x11000000003F` | `11 00 00 00 00 3F` | — (has its own layout) |
| 19791209299972 | `0x120000000004` | `12 00 00 00 00 04` | HERO68 MINI Air |
| 19791209299978 | `0x12000000000A` | `12 00 00 00 00 0A` | HERO 68 Air |
| 19791209299984 | `0x120000000010` | `12 00 00 00 00 10` | HERO 68 XS |
| 19791209299989 | `0x120000000015` | `12 00 00 00 00 15` | AULA HERO 75 HE |

A series byte, four zero bytes, and a model index. `0x11` is the wired series;
`0x12` is exactly the set the vendor handles on its wireless path. **No unit
entropy anywhere.** This is a model identifier, and the six-byte length is
confirmed by ten independent samples.

**Prediction, stated before the exchange so it can fail:** HERO 84 HE should
answer six bytes of the form `11 00 00 00 00 NN`. Its id is *not* named anywhere
in the bundle — it is one of the six unnamed `0x11` entries, or a value not in
the list at all, in which case the vendor's own layout switch falls to its
default branch. Either outcome is informative; an answer outside this shape
would mean the decode is wrong and must stop the ticket.

## 10. Why this is classified `safe_read` — and what is still assumed

For:

- It is the **first** protocol call on the connect path. The Info module's
  `initModule` runs it before constructing the service device; the device
  detector runs it on every candidate before anything else. Opening the device
  and attaching an input listener produce no traffic, and the teardown after the
  read only clears the queue and removes listeners. Nothing is sent before it and
  nothing is sent by tearing it down.
- It runs before any user interaction, on every connect, for every board in the
  module.
- Its result flows into application state (`deviceInfo.deviceId`) and selects a
  layout. It is consumed as data.
- Its command group `0x82` has no write counterpart anywhere in the bundle
  (§2), unlike `0x84`/`0x04`.
- There is no save, commit, reset, flash or reboot anywhere near it.

Not yet established:

- Nobody has observed what the firmware does with `0x82 01`. All of the above is
  an argument from the *driver*, and a driver cannot prove a firmware side
  effect. The classification is `safe_read` on `vendor_js` evidence, which is
  exactly the strength the ACL says is not enough to execute (§15).

## 11. Where it sits in the connect flow

```text
open HID device (no traffic)
  └─ construct codec  (reads the report descriptor; no traffic)
  └─ construct transport (attaches inputreport listener; no traffic)
     └─ readDeviceUUID  ◀── first bytes ever sent to the board
        └─ destroy (clears queue, removes listener; no traffic)
  └─ construct the real service device with the id that came back
     └─ getDeviceInfo  → uuid + firmware version
     └─ getOsMode, getBatteryStatus, …
     └─ initDeviceLayout switches on deviceInfo.deviceId
```

## 12. How the vendor uses the result

Two distinct uses, both pure lookups:

1. **Device selection.** The detector keys its device map by the value, so the
   id picks *which* HID device object becomes the session.
2. **Layout selection.** `initDeviceLayout` switches on `deviceInfo.deviceId`
   and picks a key-matrix layout, with a default branch for unlisted ids.

Nothing is written back, and nothing is derived from it beyond a table lookup.

## 13. Proposed typed result

The vendor calls it a UUID. The observed semantics are a model identifier, so
our model should not adopt the vendor's word:

```rust
/// The value `VendorCommand::ReadDeviceUuid` returns.
///
/// The vendor API calls this a UUID. The observed value identifies a model and
/// layout, not a unique physical unit: ten known values across the family are
/// `SS 00 00 00 00 NN`, a series byte and a model index, with no unit entropy.
/// Named for what it is observed to be, not for what the vendor calls it.
pub struct VendorModelId(u64);

impl VendorModelId {
    pub fn series(self) -> u8;   // 0x11 wired, 0x12 wireless, on current evidence
    pub fn index(self) -> u8;
}
```

`VendorCommand::ReadDeviceUuid` keeps the vendor's name — it names *their*
command, and renaming it would make the artifact harder to check against. The
decoded type does not.

If a later board ever answers with unit entropy, `VendorModelId` is wrong and
the type must split. Nothing here should be built so that finding that out is
expensive.

## 14. Proposed protocol family, and the exact evidence

**Candidate id: `aula-bytech`. Confidence: `VendorArtifact`. Not `Verified`.**

The four checks you asked for, each answered from the artifact:

| Check | Result |
|---|---|
| Shared frame format | One frame-config object exists. It is referenced in exactly two places: its own definition and the codec constructor. |
| Shared codec | One codec class. `new` is called on it in exactly one place: the SDK device class's constructor. There is no second keyboard encoder/decoder. |
| Shared command namespace | One set of services, registered by that one SDK class. Every `encode` call in the keyboard path goes through the same builder. |
| Shared response validation | One `isValidResponse`, one bubble-id set, one dongle-report test. |

And the module test: of the eleven `sdkModuleName` values that have an Info
module in this bundle (`bytech`, `sparkLinkV1`, `sparkLinkV2`, `hfd`,
`hfdMechanical`, `rlw`, `vision`, `hx`, `jm`, `ttgk`, `wyfx`), **`bytech` is the
only one whose Info module imports this SDK class.** Every other module binds a
different SDK.

So `bytech` and this codec coincide inside this artifact. That is real evidence,
and it is the vendor's own boundary rather than one we invented.

What it does **not** establish, and what keeps this at `VendorArtifact`:

- It shows the *driver* hands every `bytech` board the same encoder. It does not
  show every such board's *firmware* answers the same way. A shared encoder is
  compatible with one board ignoring a subcommand another implements.
- `bytechMechanical` is **not** evidence of a `bytech` sub-family here: in this
  bundle it appears as a UI panel name, it has no Info module of its own, and
  where the two are grouped for behaviour it is grouped *away* from `bytech`
  (tray-battery polling puts `bytech` with `hx`/`jm`, and `bytechMechanical`
  with `vision`/`hfd`/`hfdMechanical`). Treat it as out of scope, not as
  included.
- The manufacturer string `BY Tech` remains **not** evidence. The module binding
  is the evidence.

After a successful exchange, `VerifiedExchange` is earned **for HERO 84 HE on
this protocol path only**. It does not propagate to the other nine boards, and
the family stays `VendorArtifact` for them.

## 15. ACL entry — and two blockers

Intended entry:

```toml
family = "aula-bytech"

[[opcode]]
opcode   = 0x82
name     = "read_device_uuid"
class    = "safe_read"
evidence = "vendor_js"
note     = "..."
```

It cannot be written today, for two independent reasons.

**Blocker A — the schema has no subcommand.** `peripheral.opcode-acl/1` carries
one `opcode: u8` per entry, and `psafety` mints `AuthorizedCommand::opcode() ->
u8`. This family's command is a *pair*, `(0x82, 0x01)`, and `0x82` alone
authorises fifteen other subcommands including ones nobody has classified.
Granting `0x82` would grant far more than the read we analysed. Needs a schema
change: an optional `subcommand` field and a widened authorised-command type.

**Blocker B — the evidence rule forbids exactly this.** `safe_read` requires
`hardware`, `hardware_third_party` or `firmware`. `vendor_js` is a recognised
value that is deliberately *not* accepted for any executable class. The rule is
right and I am not proposing to weaken it — but it means the first exchange
cannot be authorised by the ACL, because the ACL's price of admission is the
hardware evidence that only the exchange can produce.

There is a third, smaller version of the same problem: the family declares no
measured timing, and every class a family uses must have some.

The way out is not to relax the rule. It is a separate, deliberately awkward
one-shot path — an explicit, interactively confirmed, journalled probe that
carries no `SafeCommandId` and cannot be called twice without confirming again —
whose *result* is what upgrades the ACL entry to `evidence = "hardware"` and
seeds the timing numbers. **This is a design decision, not something to settle
inside this pass**, and it is the main thing I would want agreed before any
exchange.

## 16. Proposed `SafeCommandId`

`AulaBytechReadDeviceUuid`, family `aula-bytech`, name `read_device_uuid`, class
`safe_read`. It does not exist yet and must not until §15 is resolved.

## 17. Proposed timeout

**1000 ms.** The vendor's wired transport is constructed with a 1000 ms timeout
(its wireless-8K transport gets 2000 ms, and the SDK-wide default of 500 ms is
overridden in both cases). Our board is wired — its device-table entry says
`wireless: 0`, so the wired transport is what the vendor uses on it.

For the very first exchange I would use 1500 ms: there is no retry behind it, so
a marginal timeout costs us the reading rather than triggering a resend.

## 18. Retry policy

**None. Zero attempts after the first.**

This happens to match the vendor: the wired transport is configured with
`retry: 1`, but `getUuid` overrides it to `retry: 0` at the call site — the
vendor does not retry this specific command either. That is a corroboration, not
the reason. The reason is that our safety layer has not authorised any automatic
retry, and a command whose side effects are unverified must not be sent twice
because the first answer was slow.

If it times out: report the timeout, send nothing further, and stop.

## 19. Tests and vectors, all offline

Encoder:

- `encode(0x82, 0x01, [0;6], report_id = 9)` produces the 63 bytes in §5, with
  `frame[62] == 0x6C`. Exact-bytes assertion.
- Checksum is report-id dependent: same frame with report id 1 gives `0x74`,
  with report id 9 gives `0x6C`. Locks in that the seed is real.
- Property: for any frame, `(report_id + Σ frame_with_checksum_written) % 256 ==
  255`.
- Chunking: 6 bytes → 1 packet; 56 → 1; 57 → 2; 0 → 1. Guards the `max(…, 1)`.

Decoder, from the ten known ids in §9:

- Each of the ten decodes from its six bytes to its decimal value, and
  round-trips back to the same bytes.
- `series()`/`index()` split correctly for all ten.
- Length from the frame: a response with `[5] = 6` yields 6 bytes; `[5] = 0`
  yields empty and must be an error, not `0`; `[5] = 57` must be rejected
  (exceeds the 56-byte usable window) rather than silently clamped.

Validator:

- Accepts a well-formed reply.
- Rejects each single-byte mutation of indices 0, 1, 3, 4.
- Rejects the three bubble command ids and the 19-byte dongle-report shape
  before any matching happens.
- Accepts a reply whose checksum byte is wrong, but records a checksum mismatch
  — the deliberate split from §8.

None of these need hardware. They should all be green before anything is sent.

## 20. The plan for one exchange

Not to be executed until separately approved.

1. Open `0xFF60:0x0061` on VID `0x372E` PID `0x103E`.
2. Read the report descriptor and derive the report id by the §4 rule. Assert it
   is 9; abort if the derivation yields anything else.
3. Log the exact 64 bytes about to be written.
4. Write once. Report id 9 + the 63 bytes of §5.
5. Read input reports for up to 1500 ms. For each: strip the report id, check
   the bubble/dongle shapes first, then the §8 validator. Log every report seen,
   matched or not.
6. On no match within the window: stop. Report the timeout. Send nothing else.
7. On match: record the raw 63 bytes, the length byte, the six payload bytes,
   the decoded value, and whether the response checksum matched our computation.
8. Compare against the §9 prediction. If the answer is not six bytes of the form
   `11 00 00 00 00 NN`, treat the decode as unproven and stop rather than
   rationalise it.
9. Close. Do not send a second command in the same session, including the
   firmware-version read — that is a separate decision.

Exactly one write, one read window, no retry, full transcript.

---

## Stop condition — where this pass actually stands

The command id is recovered, and the frame, checksum, validator and decoder are
all recovered with it, so the ambiguity conditions that would have halted this
are not present:

- frame format — unambiguous, and corroborated by our own report descriptor;
- checksum — recovered, and distinct from the mouse stack's;
- response matcher — recovered in full, including what it does *not* check;
- parser — recovered, and cross-checked against ten known values;
- read/write — argued from the connect path and from `0x82` having no write
  counterpart, but **on driver evidence only**.

The remaining gap is not in the protocol. It is §15: there is currently no
lawful route to send this, because the ACL requires hardware evidence for a
`safe_read` and the schema cannot express a `(group, subcommand)` pair. Those
are ours to decide, and they are the reason this stops here rather than at the
keyboard.
