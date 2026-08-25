# MCHOSE BY: the keyboard family where the A Preview blocker lives

TICKET-25 priority 5. A **separate** family from CZ: different transport,
different report ids, different frame sizes, different code base. Nothing here
transfers to CZ, to the mouse receiver, to the AA-55 OTA path, or to AULA, and
nothing from them transfers here.

No real device was involved. `assert_no_real_hid()` runs on the live page before
the first click.

---

## The transport, from the vendor's own `purify.es`

```js
async writeCommand(cmd, data, params) {
  if (this.isWired) {
    let parser = oy.getCommandConfig(cmd).wiredParser;
    if (!Array.isArray(parser)) parser = await parser();
    data.command = oy.getCommandConfig(cmd).wiredCommand;
    const bytes = await serialise(parser, data);
    await this.device.sendFeatureReport(this.currentReportId,
        new Uint8Array([...bytes, ...new Array(519 - bytes.length).fill(0)]));
  }
}

async readCommand(cmd) {
  if (this.isWired) {
    const req = oy.getCommandConfig(cmd).wiredCommand.slice(3).split(" ").map(p => "0x" + p);
    await this.device.sendFeatureReport(this.currentReportId, new Uint8Array(req));
    const reply = await this.device.receiveFeatureReport(this.currentReportId);
    const {buf} = IG(reply);            // IG(n, e=2) -> new Uint8Array(n.buffer).slice(2)
    return parser.parse(buf);
  }
}
```

A wired read is a synchronous `sendFeatureReport` / `receiveFeatureReport` pair
on one report id, with the reply's first **2** bytes dropped before parsing. All
frames are **519 bytes**, report id **6**.

Observed live, matching the `hpe` templates exactly: `87` getBattery, `83`
getKeySetting, `84` getPerformance, `8a` getLightColor, `86` getDiyLight.
(Corrected: `86` is the read-template lead byte for `getDiyLight`, per
`static/kb_command_table.json`'s own `wired_leading_pair_groups["06 86"]`; an
earlier version of this line named it `setDiyLight`, which is wired lead byte
`06` — a write, not a read, and a different byte entirely.)

## Getting a BY keyboard onto the screen

Identity comes from the vendor's own device predicates, not from similarity:

```js
W.vendorId === 1452 && W.productId === 591 && usagePage in [0xff00, 0xff04]
```

plus `getDevName`, which maps `pid 591` + a name containing `"K99"` to the model
key `K99`. Advertising exactly that made the live app show **"K99", 100%,
wired**, with `navigator.deviceHandler.isWired === true`, and the row opened the
**native Vue configurator** — not the CZ iframe — with sections
`tab-keySetting / tab-lightSetting / tab-performanceSetting / tab-otherSetting`.

The one reply this harness serves is a `getBattery` frame with the level at raw
index 8, which is where `IG`'s 2-byte drop plus the parser's 6-byte skip put it.
`synthetic_from_vendor_schema`; it says the client accepts these bytes and
nothing about a battery.

### The reply-size defect, and the wrong finding it produced

A 64-byte reply is enough for `getBattery` — the device shows 100% and the row
goes live — so it looked correct. It is not. The other reads the keyboard page
makes are far larger; the request templates carry a 16-bit little-endian length
at bytes 6–7: `0x01f8` = 504 for `getKeySetting`, `0x0200` = 512 for
`getLightColor`, `0x0080` = 128 for `getPerformance`. Answering those with 64
bytes made the vendor's parsers run off the end:

```
Offset is outside the bounds of the DataView   (x5)
Cannot read properties of undefined (reading 'flat')
```

The page's config state never loaded, and **with nothing to serialise from, no
write was ever built.** Clicking through the factory-reset confirmation produced
zero frames — which reads as *"the reset sends nothing"* and is nothing of the
kind. Replies are now sized to the vendor's own 519-byte report, and
`setPerformance` writes appear immediately.

## `setPerformance`: captured, and its settings bytes located

Driving the Performance section — polling-rate radios (`sleep / 250 / 500 /
1000`), three switches, one slider — produced **10 wired `0x04` frames, 8 of them
distinct**. Bytes that move as those controls move:

| frame offset | moves with |
|---|---|
| 7 | the polling-rate selection |
| 8, 10, 22 | the switches |

`OBSERVED_FROM_VENDOR_UI` in a synthetic environment. These are the command's own
**settings** bytes — which is exactly why they can never be the discriminator: a
discriminator has to be stable within an action, and these are not. Recording
them is what a later diff has to exclude.

## The blocker: RESOLVED — see `BY_0X04_INDISTINGUISHABLE.md`

**This section originally reported the dialog as not opening under
correctly-sized replies, `setReset` frames captured = 0, and verdict
`NOT_ESTABLISHED`. That was wrong, and is superseded, not merely amended.**

The apparent size-dependence was an artifact of driver-side polling arriving
after the vendor's own confirmation dialog had already opened and closed
(lifecycle ~6s; polling checked at 1.8s/3.5s/8s/250ms, always too late — see
`mchose_by_reset_state_diff.py`, `analysis/by_reset_state_diff.json`). An
in-page `MutationObserver` armed before the click shows the dialog opening
**in both size scenarios**, with identical text, in 12/13 tracked state
checks. There was no config-load-path dependence to explain.

With that fixed, the `setReset` frame **was captured** (519 bytes, report id
6, lead `0x04`) through the vendor's own dialog and confirm button. Byte-diff
against `setPerformance` established the wired `0x04` verdict as
**`IMPOSSIBLE_AT_WIRE_LEVEL`**: a routine `setPerformance` write and the
captured `setReset` frame are byte-identical across all 519 bytes. Full
derivation, proof, and consequences: `BY_0X04_INDISTINGUISHABLE.md`.

```
setPerformance frames captured : 10
setReset frames captured       : 1  (519 bytes, vendor dialog + confirm button)
verdict                        : IMPOSSIBLE_AT_WIRE_LEVEL
```

Wired `0x04` classification is now **final**: `setReset` is
`DESTRUCTIVE_CONFIRMED` by intent provenance (which command was issued),
`setPerformance` is `POTENTIALLY_DESTRUCTIVE`, and no function of the frame
bytes alone can ever separate them — safety for this path has to be enforced
at the point of intent, before serialisation, never by inspecting the wire
frame. A captured `0x04` frame must never be replayed.
