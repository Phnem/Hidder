# `data/`

Reviewable sources for the device registry. Everything here is human-editable and
diffable on purpose (spec.md FR4): the compiled registry the application reads is
generated from these files, never edited directly, because a binary database
cannot be reviewed in a pull request.

| Directory | Contents |
|---|---|
| `devices/` | one YAML file per device or per closely related group: identity, fingerprint signals, protocol family, capabilities with their origin, quirks |
| `layouts/` | physical key layouts and matrices, KLE-compatible on import |
| `protocols/` | per-family protocol descriptions: opcodes with their ACL class and evidence marker, checksum variant, measured timing |

Rules that apply to every file here:

- **Versioned from the first release.** Both the device-definition schema and the
  profile format carry a version. Not a v2 concern: without it the first
  community submissions become incompatible with every later schema.
- **An opcode with no explicit class is `unknown`, and `unknown` is refused.**
  Absence is never read as permission.
- **Every fact carries where it came from**: verified on hardware, read out of
  firmware, taken from vendor software, or assumed. A capability with no origin is
  not a capability.
- **Timings are measured, not guessed.** A rate limit belongs to a protocol
  family and comes from real observation on real hardware; there is no global
  default to fall back on.
- **Generated output is checked in CI**, which regenerates it and fails on any
  difference from what is committed. Hand corrections live beside the generated
  file, not inside it, with a test that fails when a regeneration drops one.

Vendor artifacts (installers, minified vendor JavaScript, decompiled bundles) are
never committed here or anywhere else in this repository. They are the input to
`tools/ingest`, which is a separate workspace precisely so that nothing touching
them can reach a release build.
