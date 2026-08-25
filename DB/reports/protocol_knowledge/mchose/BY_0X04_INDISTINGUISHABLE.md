# BY wired `0x04`: a factory reset and a settings write are the same 519 bytes

The last A Preview blocker, resolved — with an answer, not a workaround.

No hardware. `assert_no_real_hid()` runs on the live page before every run. The
factory-reset confirmation was clicked precisely because the only thing on the
other side is the harness: that is how you obtain a reset frame without sending
one to a keyboard.

---

## 1. Why the reset dialog seemed not to open

**ROOT CAUSE = the observation, not the app.**

An A/B run (`analysis/by_reset_state_diff.json`) changed exactly one variable —
the size of the synthetic `receiveFeatureReport` reply, 64 bytes vs 519 — and
compared thirteen observable states at the moment of the click. Everything
matched: route, `deviceHandler.isWired`, active tab, the visible buttons, the
reset button's `disabled` flag (`false` in both), the click landing on it. The
only divergence was downstream `RangeError` noise from the short replies.

A `MutationObserver` then showed what polling had missed: **the dialog opened in
both runs**, with its own text —

> Подсказка / Эта операция приведет к сбросу всех настроек. Вы уверены, что
> хотите восстановить заводские настройки? / Отмена / Сброс

— and closed again within seconds. Every driver-side check (1.8 s, 3.5 s, 8 s,
then every 250 ms) arrived after it was gone. Round-tripping to the browser to
look is slower than the thing being looked at.

Two of my earlier statements were therefore wrong and are withdrawn:
"the dialog opened under short replies and stopped under correct ones" (it
always opened), and the suggestion that ordering mattered (it did not — though
the reset *must* run last, because the vendor's handler ends with
`router.push({name: "ConnectDevice"})`).

The fix is an in-page `MutationObserver` that reads the dialog's text and clicks
its own confirm button (`confirmButtonClass: "red-btn"`, the vendor's own
option) the instant it appears. Still production-equivalent: the vendor's
dialog, the vendor's button, the vendor's handler.

The bundle also rules out a state guard. In the `otherSetting` component only
the two firmware items carry `if (Y.status === "disable") return;` — the reset
branch has none, so once `v(item)` runs the dialog opens unconditionally.

## 2. `setReset` captured

```
report id 6 · sendFeatureReport · 519 bytes · lead 0x04
04 00 00 01 00 80 00 01 03 02 00 00 04 04 07 00 0b 20 01 00 00 00 00 00
01 00 04 01 00 ff 06 00 00 00 01 00 01 01 00 … ff ff 04 47 04 47 …
```

Followed by `0x0a`, `0x06` and three `0x03` frames — the rest of the sequence.
Only the first is `setReset`.

Those bytes are `x8.K99.otherObj` (`Okt`) serialised: `space0 [0,0,1,0,128,0]`,
`rateVal 1`, `space2 3`, `latencySwitch 2`, `space3 [0,0,4,4,7]`,
`lightMode 11`, `space4 [32,1,0,0]`, `space5 [0,0,1,0,4,1,0,255]`,
`sleepTimeVal 6`, `space7 […,255,255]`. The capture and the static constant
agree independently.

## 3. The discriminator, and why it cannot exist

### The first answer was wrong

A naive diff reported `DISCRIMINATOR_FOUND` with 40 differing offsets. It should
not have been believed: on the `setPerformance` side every one of those offsets
was **zero**, and the zeros came from this harness answering `getPerformance`
with zeros. The reset frame carries factory defaults, which are not zeros. That
diff measured the harness.

### The experiment that settles it

Give the page a device whose stored performance record *is* the factory default,
by answering `getPerformance` with the captured reset payload, realigned. The
alignment is the vendor's own: the read parser `Oie` skips 2 dropped + 6
`space0` bytes; the write emits 1 command + 6 `space0`; so `reply[2:] =
write[1:]`. The layouts are otherwise identical field-for-field — only the names
`space1` and `macSwitch` occupy each other's slot, which changes no bytes.

Then, through the vendor's UI, toggle one switch and toggle it back.

```
routine:toggle_on    04 … 03 00 00 …      (offset 10 = 0x00)
routine:toggle_back  04 … 03 02 00 …      (offset 10 = 0x02)
reset_confirm        04 … 03 02 00 …      (offset 10 = 0x02)

routine:toggle_back == reset_confirm  →  TRUE, all 519 bytes
toggle_on vs toggle_back             →  differ at exactly one offset: 10
```

**A routine settings write and a factory reset are byte-identical.**

### Why this is structural, not incidental

From `hpe`: both commands carry `wiredCommand: "04"`, and both serialise through
the same function — `setPerformanceWiredParser: () => j_(!0)` and
`setResetWireParser: () => j_(!0)`. From `writeCommand`, the frame is
`serialise(parser, data)` padded to 519: a pure function of parser and data.
From `otherSetting`, the reset's data is the constant `x8[model].otherObj`.

So `setReset` is not a distinct command on the wire. It is `setPerformance`
carrying one particular value of the same record — and that value is the one a
factory-fresh keyboard already reports, so the settings UI reaches it by doing
nothing at all.

## 4. What follows

**`WIRED 0x04 DISCRIMINATOR = IMPOSSIBLE_AT_WIRE_LEVEL`.**

This closes the question rather than the risk. The consequences are hard rules:

* **Frame inspection cannot make this path safe.** Any classifier that reads a
  wired `0x04` frame and returns "safe" is wrong on a reset; one that returns
  "destructive" is wrong on every settings write. Neither is available.
* **Safety must be enforced at the point of intent** — which command is being
  issued — not by looking at bytes.
* **A captured `0x04` frame must never be replayed.** Replay cannot know what it
  is replaying.
* `setPerformance` stays **POTENTIALLY_DESTRUCTIVE**, and that classification is
  now *final* rather than pending: there is nothing further to learn from the
  wire.

Pinned by `tests/test_mchose_by_0x04_indistinguishable.py` (9 tests), including
one that was checked to fail when the two frames are made to differ.
