# `docs/hardware/`

Read-only HID inventories of reference devices. These are **evidence**, not
configuration: they record what an operating system reports about a physical
board at a point in time, so that later claims about that board can be checked
against something rather than remembered.

## Files

| File | What it is |
|---|---|
| `<device>.json` | machine-readable capture, schema `peripheral.hid-inventory/1` |
| `<device>.md` | the same capture rendered for reading, including the raw descriptors |

Both are **generated**. Do not hand-edit them; rerun the tool. Analysis and
conclusions belong in the ticket and `EXECUTION_LOG.md`, not in a generated file
that the next run will overwrite.

## How to capture

```bash
cargo run -p ptransport --example inventory
```

lists every HID collection on the machine, so you can find the device's VID:PID.
Then:

```bash
cargo run -p ptransport --example inventory -- --device 372E:103E --label "AULA Hero 84 HE" --out docs/hardware/aula-hero-84-he
```

**Every device is captured by this same command.** There is no per-device tool
and no per-device flag beyond the label and the path. If some device ever needs
a different code path, that is a finding about `ptransport`, to be fixed in
`ptransport` — not worked around in a script. Comparability between captures is
the whole point: two devices inventoried this way diff line by line.

## What is and is not touched

The capture is read-only in a strict sense, and the strictness matters because
this code runs against hardware that can be bricked:

- no writes, no output reports;
- no feature reports **sent**. Reading one still means asking the firmware a
  question, which is a probe, and probes belong behind the safety gate;
- no input reports read. An input report from a keyboard collection is a
  keystroke.

What remains is what the OS already holds: enumeration entries, cached string
descriptors, and the report descriptor (on Windows the backend rebuilds that
from preparsed data, so not even the descriptor costs traffic on the wire).

## Reading the results

**`opened: true` does not mean "we can talk to it."** On Windows the backend
falls back to opening a device with no access rights when read/write is refused,
and that still yields descriptors and strings. Telling the two apart requires
sending a report, which a capture must not do. So treat `opened` as
"enumeration-level access succeeded" and nothing more.

**Report sizes are in bytes on the wire**, including the leading report-ID byte
when the descriptor numbers its reports. A descriptor that uses no report IDs
has no such byte, and a reader that assumes one is off by one on every field.

**`report_descriptor_fnv1a64` is a comparison aid, not a fingerprint.** The
registry's descriptor hash is a product decision that belongs in `pregistry`;
this one exists so two captures can be compared by eye.

**Serial numbers are withheld by default.** The report records whether a serial
exists, because presence is a fingerprint signal, but not its value: it uniquely
identifies one physical unit, these files get committed, and community device
submissions will eventually use this same shape (TICKET-18). `--include-serial`
overrides that for local debugging; do not commit the result.

**A device path is not an identity.** It is recorded so a run can be reproduced
on the same machine. On Windows it changes between reboots.

## Completeness

`hidapi` enumerates HID collections. A device could in principle expose a
non-HID interface (WinUSB, CDC) that no HID capture would ever show. Where that
matters, confirm the interface count separately from the OS — on Windows:

```powershell
Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -like "*VID_XXXX&PID_YYYY*" }
```

and check that the number of `MI_xx` interfaces matches what the capture found.
