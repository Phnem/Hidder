# MCHOSE: how a device reaches the configurator, and where a fake one stopped

TICKET-25, points A–D. Written after three oracle passes had each fixed a
different click defect and each still ended at "the configurator never opens" —
which is what a symptom looks like when the cause is upstream of the click.

**CONFIGURATOR ENTRY = CLOSED.** Reproduced across runs. Route reaches
`/#/keyboard?deviceName=MCHOSE+God+60`, the device list row is live, and with a
normal-mode identity the vendor's own code begins writing frames.

No real device was involved. `assert_no_real_hid()` runs on the live page before
any interaction and refuses to continue unless `navigator.hid` carries the
harness's own marker.

---

## A. The transition, traced statically

Every arrow below is quoted from the shipped bundle, not inferred.

```
device row click
  → F4 = item => { toDeviceAliveStatus(item).isDisabled || D3(item) }     [index-BEwRBt7C.js]
  → D3: czRemoteConfigList.match(item).webdriverEnum.deviceType === KEYBOARD
  → localStorage deviceName + lastKBProductKey
  → router.push({name: "Keyboard"})                                       [route /keyboard, no guard]
```

and the state the row depends on:

```
requestDevice
  → getDeviceMap()  → navigator.deviceMap, gM.renewDevicesList(czDevices)  [purify.es]
  → setupDeviceSDK(dev)         requires window.loadCZSharedData
  → clampDevices([dev])         rejects with Error("device not allow")
  → createDeviceSDK(dev)
  → device runtime → SingletonDeviceStore.updateDeviceState(dev, …)
  → list-item mapper: if (deviceState) item.connectMode = isWireless ? 1 : 0
```

The mapper is the load-bearing part (`purify.es`, `useFillListTemp`):

```js
const h = deviceState(item._getHidDevice());
if (h) { … A = {connectMode: h.isWireless ? 1 : 0, isBusy: h.isBusy, …}; … }
else   { defaults; }          // gnt() — and gnt() has NO connectMode
```

against `helper-C1sPqtX4.js`:

```js
function toDeviceAliveStatus(e) {
  const isInvalid = !!(e.isSleepElectron || e.connectMode == null
                       || e.batteryLevel === "breakConnect" || …);
  const isLoading = !!(e.connectMode == null || e.isBusy);
  return { isInvalid, isLoading, isDisabled: isInvalid || isLoading, … };
}
```

**No device state ⇒ no `connectMode` ⇒ `isDisabled` ⇒ the click is discarded
with no console error and no frame.** That is the whole of the earlier symptom.
On this host the CZ SDK settles around **t+21 s**, which is later than every
fixed wait the oracle had used. `wait_device_ready()` now waits on the state
instead of the clock.

Ruled out statically, so they did not need testing: the first-guide overlay
(`firstGuideType` is `"disabled"` unless device state exists, so it cannot be
what blocks a device that has no state), and any route guard (the only global
guard covers `meta.desktopOnly`, which `/keyboard` does not set).

## B. What the live trace said

`mchose_transition_trace.py` walks thirteen tripwires and reports the last that
passed. It is observation-only: no wrapper supplies a value the vendor code did
not compute, none suppresses an error, and a wrapped call that throws is
recorded and re-thrown unchanged.

Result: `clampDevices` → `ALL_KEPT`. `createDeviceSDK` → ok, storageKey
`God 60`. Store size 1, `isBusy:false`, `communicateEnabled:true`,
`failedCount:0`. `localStorage` tripwire fired. `pushState` to
`#/keyboard?deviceName=MCHOSE+God+60`. The chain completes.

## C. Two contract mismatches, both of which produced wrong FINDINGS

### C.1 The grant is per-origin, not per-realm

MCHOSE serves its CZ keyboard configurator as a **separate Next.js app under
`/cizhou/`, mounted in an iframe** — a fifth code base, alongside the four
transports the static lane already separates.

Real WebHID records a grant against the origin, so that iframe sees the device
in `getDevices()` without ever calling `requestDevice`. The fake runtime kept
grant state in a module-scoped boolean, which gets a fresh copy per frame realm.
The iframe therefore polled `getDevices()` ~10×/s and threw:

```
Error: No HID devices found
  at .../cizhou/_next/static/chunks/5509-….js
```

Read at face value that says *the vendor app does not support this device*.
It says nothing of the kind. Fixed by scoping the grant to the origin
(`sessionStorage`), keeping the deliberate fresh-pairing default. Pinned by
`tests/test_fake_runtime_grant_scope.py`, which was checked to fail when the
old behaviour is put back.

### C.2 `0xff00:0x0001` is the BOOTLOADER, not the config channel

This table was wrong, and it was wrong in an inherited shape.

Asking the vendor's own `isBootUpdateMode(vid, pid, name)`:

| product | normal (`0x0001:0x0000`) | DFU (`0xff00:0x0001`) |
|---|---|---|
| God 60 | `0x3020` | `0x301a` |
| Ace 75 8K | `0x303c` | `0x303d` |
| Ace 75 16K | `0x301d` | `0x301f` |
| Ace 68 GT | `0x3007` | `0x3009` |
| Ace 60 Pro ISO FR | `0x3040` | `0x3041` |

The oracle had been advertising the DFU id for all three profiles. Observed
consequence: the live app opened *"the current device is in upgrade mode —
download the latest firmware?"* instead of the configurator, and the runtime
guarded nearly every operation with `if (isUpgradeMode) return`, so a
zero-frame capture was guaranteed.

Naming the error precisely, because it is the transferable lesson: assuming
`0xff00:0x0001` was "the vendor config channel" is **AULA's shape** — and it was
not even right there, where the config collection sat at `0xFF60:0x0061` on the
same product id and `0xFF00:0x0001` was explicitly *not* the config channel. The
assumption was wrong on both vendors. It survived because "the two ids are
generic + config" is a plausible sentence, and nobody asked the vendor.

## D. What the device is asked, once it is the right device

With `0x3837:0x3020` the app writes, repeatedly, one frame:

```
sendReport(reportId=0, 64 bytes)
55 03 00 38 38 00 00 … 00
```

Unanswered (the harness replies with nothing by default, so anything that looked
like a reply could only have come from the page). The runtime therefore enters
its error life-cycle — `communicateEnabled:false`, `isBusy:true`,
`failedCount` climbing, retry every 3 s — and the row stays disabled.

**This frame belongs to a family the static lane has not characterised.** It is
not any of the four transports already separated in
`docs/prior-art/mchose-static-lane.md`: the leading byte is `0x55`, report id 0,
64 bytes, and it is emitted by the CZ SDK, whose code is not in the acquired
bundle set at all. It is recorded here as an observation and **not** merged with
the keyboard `hpe` table, which belongs to the BY family.

Consequence for the A Preview blocker: `setPerformance` / `setReset` — the wired
`0x04` pair whose frame shapes are indistinguishable — are **BY-family**
commands (`navigator.deviceHandler.sendCommand`, `hpe` table). God 60, Ace 75,
Ace 68 GT are all **CZ** (`webdriverEnum.subType == 2`). Byte-diffing that pair
requires driving a **BY** keyboard (G87, K99, G98, UT98, Z75, X75, GX87, K87,
G75_Pro), which needs a synthetic `getBattery` reply built from the `hpe`
schema, marked `synthetic_from_vendor_schema` and never treated as hardware
evidence. That work is not done, and the blocker stays OPEN.

## Identity lane

`mchose_cz_identity_sweep.py` asks the CZ SDK's own lookups for every identity
in `HidIndexDeviceFilters`. Each row carries the identity tuple and the name in
one record, so no edge rests on similarity, vendor id, or family.

* **66 of 125** identities resolve to a real name — **21 distinct products**.
* **59** return the SDK's *fallback*: `deviceName:"Ace60"` with empty
  `fullName`/`storageKey` and null `glbKey`. Recorded as
  `SDK_FALLBACK_NOT_A_NAME`. Counting those as names would have manufactured 59
  edges claiming that every MCHOSE mouse, headset and BY keyboard is called
  "Ace60" — a fabricated edge wearing a vendor's clothes, which is worse than an
  unresolved one.
* **22 of the 25** previously unresolved keyboard ids are now named from this
  source. Three remain: `0x3837:0x302d`, `0x3837:0x3030`, `0x3837:0x303e`, which
  appear in no CZ filter entry.

`isBootUpdateMode` is the vendor's claim about its own firmware, not a hardware
observation — recorded as such. For God 60 it is corroborated by observation:
advertising `0x301a` made the live app open its firmware-update dialog.
