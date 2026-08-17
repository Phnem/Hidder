//! Learning mode.
//!
//! Skeleton only. Built in the phase 4 epic (TICKET-16).
//!
//! Four levels, in order, each a gate rather than a suggestion (spec.md FR5):
//!
//! 1. **Inventory** -- always safe, reads nothing but what enumeration gives.
//! 2. **Guided capture** -- diff a vendor-driver change across three or four
//!    observations, not two. Two points fit too many hypotheses.
//! 3. **Safe probe** -- allowlist only, with a mandatory anti-fiction filter: a
//!    reply identical to the previous reply counts as "not supported", because
//!    that is what an unsupported opcode most often returns. The filter lowers a
//!    hypothesis; it never promotes one, and it never concludes `Unsupported`
//!    from silence.
//! 4. **Verified write** -- backup, dry run, one write, and confirmation that is
//!    *not* the device's own answer: a read-back, the analog stream, a physical
//!    check, or the user's eyes.
//!
//! # Privacy is a behaviour, not a paragraph
//!
//! Keyboard input collections are filtered out in code, and a test proves no
//! input report from a keyboard collection can reach a capture log (spec.md
//! FR8). The same discipline extends to anything a user might paste into a bug
//! report: a wire-trace log records direction and opcode only, never payload,
//! because a payload can carry a keymap and a keymap can carry a macro with a
//! password in it.
