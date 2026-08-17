//! Protocol engines.
//!
//! Skeleton only. The first engine (AULA, read-only) arrives with TICKET-12.
//!
//! # What this crate hides
//!
//! Opcode-level detail, behind one trait for every family: probe, capabilities,
//! read, write, verify, stream. `verify` is mandatory per capability, never
//! optional (spec.md FR1) -- and what counts as verification is per capability
//! and per board: read-back, indirect read-back, the analog stream, or a human
//! confirming physically. Do not lock the verification result into pass/fail
//! before TICKET-08/09 show what is actually readable on real hardware.
//!
//! # Opcodes are resolved per family, never from a global constant
//!
//! Families collide. Documented cases in one OEM family: the same byte writes
//! the keymap in one sub-family and an options bitfield in another; another byte
//! is debounce in one and a sleep timer in the other; even factory reset swaps
//! numbers between the two (docs/prior-art/royuan.md). A misaddressed write
//! lands on a live register.
//!
//! So: every family-dependent command resolves through a per-family table, and
//! a missing entry is an explicit absence rather than a zero or a default -- the
//! table says "this family has no such command, or its opcode is not
//! documented", which are both reasons to refuse, not to guess.
//!
//! Consequence: this crate must not expose global opcode constants at all. The
//! moment a second family exists, a constant belonging to the first becomes a
//! trap.
//!
//! # Checksums belong to the opcode
//!
//! In the first family studied, two checksum variants differ by which byte range
//! they cover, and which one applies is decided by the opcode, not by the device.
//! A codec that picks by device gets lighting writes wrong.
//!
//! # Provisional by construction
//!
//! Do not treat this trait as stable after one engine exists. One family cannot
//! validate a trait's generality; it stays provisional until a structurally
//! different family is implemented against it (TICKET-17).
