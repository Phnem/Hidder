# Ember — the canonical shape of an actuation setting

Reference note for TICKET-12's HE work. Read to answer one question: *how should
a Hall-effect actuation setting be thought about?* Not to learn anything about
AULA, which encodes it differently and whose bytes come from its own artifact.

## Provenance and licence

| Field | Value |
|---|---|
| Project | Ember, a 32-key Hall-effect keyboard (firmware, PCB, web configurator) |
| Local checkout | `_prior-art/ember/` — never committed; `_prior-art/` is gitignored |
| Transport | USB CDC with COBS framing. **Not HID.** Transport-irrelevant to us |
| Licence | **None found.** No `LICENSE`, `COPYING`, or licence header in the repository |

The missing licence is the load-bearing fact here. Under this project's rule
that an unknown licence is a forbidden one (`docs/prior-art/inventory.md`), this
is **`docs` mode only**: read for vocabulary and semantics, nothing copied,
nothing ported, no ranges adopted. Everything below is a restatement in our own
words of what the interface *means*, which is the same footing on which
`minipad-firmware` and `libhmk` are used.

## Actuation canonical model

**Scope** — per key. Every key carries its own five-byte config block; there is
no global actuation setting and no per-profile one.

**User-facing unit** — millimetres. The configurator's slider is labelled in mm
and its values are decimal (`0.5` … `4.0`).

**Internal representation** — one unsigned byte in units of 0.1 mm. Host side,
that is one constant applied in both directions: `raw × 0.1 = mm` on read,
`round(mm ÷ 0.1)` clamped to a byte on write. There is exactly one place in the
host code where the scale exists, which is the property worth copying — the
idea, not the number.

**Range and precision** — the configurator offers 0.5–4.0 mm in 0.1 mm steps for
actuation, and 0.1–1.0 mm in 0.1 mm steps for each rapid-trigger sensitivity.
The firmware's travel computation saturates at 4.0 mm, so the UI maximum and the
physical maximum agree. Firmware default is 1.0 mm; the configurator's own
default is 2.0 mm — they disagree, which is itself a reminder that a default is
not a capability.

**Read semantics** — reading a key's config returns the stored setting: the
number the user chose, not where the key currently is.

**Write semantics** — the same byte, and a separate explicit "save config"
command afterwards. Setting a value and persisting it are two operations.

## The four things that must not be conflated

This is the part worth carrying into our own model, because all four are "a
number about how far a key is pressed" and only one of them is a setting.

| Concept | What it is | Direction |
|---|---|---|
| **Actuation point** | The depth at which the key first registers. A *setting*. | read/write |
| **RT down sensitivity** | How far the key must travel back *down* from its shallowest point to re-trigger. A *setting*, and a delta, not a depth. | read/write |
| **RT up sensitivity** | How far the key must travel back *up* from its deepest point to release. A *setting*, and a delta. | read/write |
| **Calibration data** | The raw sensor extremes for that key, min and max. Not a depth at all, and in raw ADC units. | read only |
| **Push distance** | Where the key is *right now*. An observation, changing continuously. | read only |

Ember keeps all five in separate address ranges, and only the first three are
writable. The two rapid-trigger values are deltas measured from a moving
reference point, while actuation is an absolute depth from the top of travel —
same unit, different meaning, and a decoder that returned them as one "actuation"
number would be wrong in a way no unit check would catch.

**Direction convention**: position increases with depth. Zero is released, the
maximum is bottomed out, and a key is pressed when position exceeds the actuation
point. Worth stating because the opposite convention is equally plausible and
the difference is invisible in a byte.

**Calibration's role**: raw sensor value plus that key's calibrated min and max
produce a physical distance. So calibration is *upstream* of every distance in
the system, and two keys reporting the same raw value can be at different
physical depths. It is not a setting a user tunes; it is what makes a setting
mean the same thing on every key.

## What this note does *not* license

- No opcode, address, packet layout, or framing from Ember reaches AULA work.
  Ember is CDC/COBS with a 16-bit address map; `aula-bytech` is HID with a
  group/subcommand pair. Nothing transfers.
- No range. If AULA turns out to offer 0.1–3.5 mm, that is what it offers.
  Ember's 0.5–4.0 mm is one board's answer, and adopting it because it looks
  familiar is the same error as adopting the mouse stack's checksum.
- No calibration mathematics. Ember fits a logarithmic curve with a
  hand-tuned constant to its own sensors. AULA's relationship between raw value
  and travel is unknown and must come from AULA's own artifact.
- No conversion factor. `× 0.1` is Ember's. AULA's must be **proved** from the
  vendor bundle: a device saying `37` does not mean 0.37 mm because 0.37 looks
  like a plausible actuation point.

## What it does license

A checklist for reading the AULA artifact, and for our own `he.actuation`:

1. Is it per key, per profile, or global?
2. What unit does the vendor's own UI display?
3. What is the wire representation, and what exactly converts one to the other?
4. What are the range and step, according to the vendor rather than to us?
5. Are actuation and the two rapid-trigger deltas separate fields, and are the
   deltas measured from a moving reference?
6. Is the value being read a setting, a calibration constant, or a live position?

Question 3 is the one that has to be answered from the artifact before any value
we display can be called millimetres.
