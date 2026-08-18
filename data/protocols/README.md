# `data/protocols/`

One file per protocol family. Each file is the ACL for that family's opcodes and
the measured timing for each class of write it permits. `psafety`'s build script
reads these files and generates the closed `SafeCommandId` enum from them
(spec.md FR2, TICKET-11).

**Being listed here grants nothing.** A command in this directory still needs an
engine that speaks the family, a registry match at `confidence >= Verified` for
the device in hand, and a device that is actually connected. This directory
decides only what is *impossible*: an opcode that is absent, `unknown` or
`destructive` here cannot be turned into an executable command by any later
code, because no `SafeCommandId` exists for it and the executor accepts nothing
else.

## Format

`schema = "peripheral.opcode-acl/2"`. TOML rather than the YAML used by
`devices/`: this file is read by a build script, and the parser that reads it is
part of the safety boundary, so it uses a maintained parser with unknown fields
rejected. A typo in a field name must fail the build, not be ignored.

```toml
schema       = "peripheral.opcode-acl/2"
family       = "royuan-gen2"          # kebab-case id, matches the registry
display_name = "ROYUAN gen2"
note         = "why this family exists as its own ACL"

[timing.class.safe_read]
min_gap_before_ms = 12
settle_after_ms   = 0
evidence          = "hardware_third_party"
note              = "where the number was measured"

[[command]]
opcode   = 0x8F
name     = "identify"
class    = "safe_read"
evidence = "hardware_third_party"
note     = "why it is in this class"
```

## A command is a key, not a byte

An entry states its identity in exactly one of two shapes, and the generator
refuses anything else:

```toml
opcode     = 0x8F                     # one byte: the ROYUAN families

group      = 0x82                     # two bytes, one identity: aula-bytech
subcommand = 0x01
```

Half a pair is an error, both shapes at once is an error, and a bare `opcode`
may not share its leading byte with any `group` in the same family. That last
rule is the point of the whole change: `aula-bytech` addresses sixteen
subcommands under `0x82`, most of them unclassified, so authorising the bare byte
would authorise all of them.

## Classes

| Class | Meaning | Generates |
|---|---|---|
| `safe_read` | reads state, changes nothing | `SafeCommandId` |
| `probe_ok` | a production read whose purpose is identifying an unknown board | `SafeCommandId` |
| `bootstrap_probe` | read-only, believed safe on a vendor artifact, never yet sent from here | `ProbeCommandId` |
| `safe_write` | writes volatile state | `SafeCommandId` |
| `slow_flash` | writes state that survives a power cycle | `SafeCommandId` |
| `destructive` | erases, resets, or enters a bootloader | **nothing, ever** |
| `unknown` | not classified, or classified on insufficient evidence | **nothing, ever** |

`bootstrap_probe` is the one door a vendor artifact can reach, and it is narrow
on purpose: a different enum, a different gate, one explicit confirmation per
send, no retry, no batch, and a typed decoder that has to accept the answer
before it becomes a value. It exists because a `safe_read` is earned with
hardware evidence and hardware evidence is earned by sending the command once —
without somewhere for that first send to live, the rule is unsatisfiable and the
pressure is to put a hole in it instead. A command does not stay in this class:
it is sent, the answer is reviewed by a person, and the entry is rewritten by
hand as a `safe_read` on `hardware`. Nothing in the program performs that
rewrite.

An opcode that is not listed at all is `unknown`. Absence is never permission.

## Rules the build script enforces

These fail the build rather than warn, because each one is a way an unsafe
command could otherwise be generated quietly:

1. **Every entry states its class, evidence and note.** A missing field is an
   error; there is no default class.
2. **`safe_write` and `slow_flash` require `evidence = "hardware"`** — verified
   on hardware by this project. Prior art's word is not enough to earn a write:
   record the opcode as `unknown` until it has been verified here.
3. **`safe_read` and `probe_ok` require `hardware`, `hardware_third_party` or
   `firmware`.** A vendor artifact and an assumption do not qualify, and this
   rule did not move when `bootstrap_probe` arrived — that class was added
   precisely so this one would not have to bend.
4. **`bootstrap_probe` additionally accepts `vendor_artifact`**, and never
   `assumed`. A probe is a real command reaching real firmware; "we think this is
   a read" is not a reason to send one.
5. **`bootstrap_probe` must not declare timing.** It performs one operation per
   authorisation with the gate consumed in the act, so there is no second
   operation for a cadence to sit between.
6. **Every other class a family uses must have measured timing**, and a `slow_flash`
   class must declare a non-zero settle period. A flash write with no measured
   quiet period afterwards is the exact shape that wedges a board.
7. **No duplicate key and no duplicate name within a family.**
8. Timing is per family. There is no global default and no fallback: an unknown
   family gets one operation at a time with explicit confirmation, never an
   invented interval.

## Why per family and not global

The same byte means different things in different families. In this directory
already: `0x06` is Options on yc500 and Debounce on gen2; `0x09` is Keymatrix on
yc500 and Options on gen2; factory reset is `0x02` on yc500 and `0x01` on gen2.
Sending "reset" to the wrong family is sending an unknown command
(`docs/prior-art/royuan.md`).
