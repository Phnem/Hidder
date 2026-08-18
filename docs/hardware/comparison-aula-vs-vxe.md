# Comparison: AULA Hero 84 HE vs VXE Dragonfly R1 SE+

Hand-written analysis, unlike the `*.json`/`*.md` captures in this directory,
which are generated. Numbers here are copied from those captures; if they ever
disagree, the captures are right.

Produced by TICKET-22. Sources:

| Column | Capture |
|---|---|
| AULA | `aula-hero-84-he.{json,md}` (TICKET-08) |
| VXE wired | `vxe-dragonfly-r1-se-plus-wired.{json,md}` |
| VXE receiver | `vxe-dragonfly-r1-se-plus-24ghz.{json,md}` |
| VXE BT | not captured — see "Bluetooth" below |

## Side by side

| | AULA Hero 84 HE | VXE wired | VXE receiver |
|---|---|---|---|
| VID:PID | `372E:103E` | `3554:F58F` | `3554:F58E` |
| release (`bcdDevice`) | `0x0216` | `0x0315` | `0x0110` |
| manufacturer | `BY Tech` | `Compx` | `Compx` |
| product | `HERO 84 HE` | `VXE R1SE+` | `VXE Mouse 1K Dongle` |
| serial | present, withheld | present, withheld | present, withheld |
| USB interfaces | 3 | 3 | 3 |
| top-level collections | 7 | 8 | 8 |
| vendor-defined TLC | 2 | 4 | 4 |
| usage pages seen | `0x0001`, `0x000C`, `0xFF00`, `0xFF60` | `0x0001`, `0x000C`, `0xFF02`–`0xFF05` | identical to wired |
| descriptor bytes, total | 322 | 314 | 314 |
| collections opened | 7 of 7, no admin | 8 of 8, no admin | 8 of 8, no admin |
| battery usage | none | none | none |
| connection | USB only | USB | 2.4 GHz |

### Collections

AULA Hero 84 HE:

```text
if0  0001:0006  boot keyboard      rid  -   in  8   out 1
if1  0001:0002  mouse              rid  2   in  5   feature 2
if1  0001:0006  keyboard NKRO      rid  7   in 16
if1  0001:0080  system control     rid  5   in  2
if1  000C:0001  consumer control   rid  4   in  3
if2  FF00:0001  vendor             rid  3   feature 64        ← feature only
if2  FF60:0061  vendor             rid  9   in 64 / out 64
```

VXE, **identical in both wired and receiver captures**:

```text
if0  0001:0006  boot keyboard      rid  -   in  8   out 1
if1  0001:0080  system control     rid  3   in  2
if1  000C:0001  consumer control   rid  5   in  3
if1  FF02:0002  vendor             rid  8   in 17 / out 17    ← config candidate
if1  FF03:0000  vendor             rid  2   in  8
if1  FF04:0002  vendor             rid  6   feature 8         ← feature only
if1  FF05:0000  vendor             rid 16   in  8
if2  0001:0002  mouse              rid  -   in  7
```

### Descriptor fingerprints (fnv1a64, first 8 hex digits)

| Collection | AULA | VXE wired | VXE receiver |
|---|---|---|---|
| `0001:0006` boot kbd | `109a9237` | `d681df95` | `d681df95` |
| `0001:0002` mouse | `cbf68767` | `25077c26` | `25077c26` |
| `0001:0080` system | `a0541972` | `80e01dbf` | `80e01dbf` |
| `000C:0001` consumer | `6a7267dc` | `365d4c9a` | `365d4c9a` |
| vendor #1 | `c0e89e9e` (`FF00:0001`) | `c2bb04bd` (`FF02:0002`) | `c2bb04bd` |
| vendor #2 | `57273a38` (`FF60:0061`) | `63b37376` (`FF03:0000`) | `63b37376` |
| vendor #3 | — | `deacbb83` (`FF04:0002`) | `deacbb83` |
| vendor #4 | — | `b760328a` (`FF05:0000`) | `b760328a` |

**Every VXE descriptor is byte-identical between the mouse and its receiver.**

## What differs between the two VXE modes

Three fields. Nothing else, at all:

```text
product_id       0xF58F (wired)              0xF58E (receiver)
product string   "VXE R1SE+"                 "VXE Mouse 1K Dongle"
release_number   0x0315                      0x0110
```

Same vendor id, same manufacturer string, same interface layout, same eight
collections, same report ids, same report sizes, same descriptor hashes.

## The finding that matters for the registry

The specification ranks fingerprint signals from strongest to weakest as:
report-descriptor hash → TLC set → manufacturer/product strings → identify
opcode → firmware version → VID:PID, with VID:PID called out as the weakest and
"index only".

On this hardware that ordering is inverted. The descriptor hash and the TLC set
— the two strongest signals — **cannot distinguish the receiver from the mouse**,
because they are identical. The only signals that can are the product string,
the release and the VID:PID: the three weakest in the chain.

This does not make the ordering wrong. It makes it incomplete: the strong
signals answer "what shape of device is this, what protocol might it speak",
and the weak ones answer "which physical thing am I talking to". `pregistry`
needs both, and TICKET-10 has to say which question each weight is for rather
than inheriting a single ranking. Recorded as an input to that ticket, not
fixed here.

## Receiver behaviour

The receiver was captured three times, in three states of the mouse:

| Mouse state | Receiver HID surface |
|---|---|
| in active use over 2.4 GHz | 8 collections |
| on the cable (radio idle) | identical, no field differs |
| powered off entirely | identical, no field differs |

So the receiver is a self-contained HID device whose collections do not reflect
what is behind it. It presents a mouse collection, a keyboard collection and
four vendor channels whether or not any mouse exists. No separate logical device
appears behind it, and nothing in the enumeration distinguishes "receiver with a
mouse paired" from "receiver alone".

Practical consequence: a device list built from enumeration alone will show the
dongle as a mouse that is always present. Telling the difference requires asking
the firmware, which is a probe, and probes are out of scope here.

## Bluetooth

**Not captured.** The mouse's switch has a Bluetooth position, but pairing with
this PC did not succeed during TICKET-22.

What was ruled out: the host's Bluetooth stack works — `Generic Bluetooth Radio`
is present and other devices are paired with it. So the absence of a Bluetooth
HID for this mouse is not explained by a missing radio on this machine.

What is therefore **not** claimed: that this unit does not support Bluetooth. The
observation is inconclusive, and inconclusive is what it is recorded as. A later
successful pairing would add a fourth column here.

## Which data belongs to what

| Datum | Belongs to |
|---|---|
| `3554:F58E`, `VXE Mouse 1K Dongle`, release `0x0110` | the receiver |
| `3554:F58F`, `VXE R1SE+`, release `0x0315` | the mouse itself, over its own cable |
| the eight collections and their descriptors | **both, indistinguishably** — the receiver presents the same set |
| serial number | per physical unit; present in both, withheld from these files |

The middle row is the awkward one, and it is the point of this table: the
descriptor set is not evidence about which of the two objects is on the other
end of the wire.
