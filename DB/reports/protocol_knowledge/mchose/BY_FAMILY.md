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
getKeySetting, `84` getPerformance, `8a` getLightColor, `86` setDiyLight.

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

## The blocker: still OPEN, and now precisely located

The factory reset exists and the app names it in its own words. Clicking
**"Сброс"** under *"Восстановление заводских настроек"* opens:

> Эта операция приведет к сбросу всех настроек. Вы уверены, что хотите
> восстановить заводские настройки? — [Отмена] [Сброс]

That dialog was reached and read. What has **not** been captured is the frame it
produces. Three click methods were tried — synthetic `el.click()`, a real mouse
click at the element's centre, and Playwright's actionability-checked
`locator.click()` — and in the runs with correctly sized replies the dialog did
not open at all, while in an earlier run with **under**-sized replies it opened
every time and then emitted nothing (for the parser reason above).

That inversion is suspicious and is recorded as an open question rather than
explained: the button's behaviour appears to depend on which config-load path
the page took. It is a harness/UI question, not a protocol one, and it does not
require hardware.

**So:**

```
setPerformance frames captured : 10
setReset frames captured       : 0
verdict                        : NOT_ESTABLISHED
```

An absent frame is a gap in the walk, not evidence that the command does not
exist. Wired `0x04` remains **POTENTIALLY_DESTRUCTIVE**: `setPerformance` and
`setReset` still share a lead byte and a parser, and nothing yet separates them
by value.
