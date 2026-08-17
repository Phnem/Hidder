//! The write gate.
//!
//! Skeleton only. Real enforcement arrives with TICKET-11.
//!
//! # The one invariant the whole safety story rests on
//!
//! There is exactly one path from a user's intent to a byte reaching a device,
//! and it runs through this crate. Any code path that reaches an engine's write
//! logic without passing here is a blocking defect, not a style question
//! (spec.md FR2/FR3).
//!
//! In a release build the executor accepts only a `SafeCommandId`: a closed enum
//! generated at build time from registry entries whose class is neither
//! `destructive` nor `unknown`. A raw opcode from a data file cannot reach the
//! transport without rebuilding the binary. Unknown is the default class, and
//! unknown means refused.
//!
//! # Rate limiting
//!
//! A property of the protocol family, not a global constant. For a known family
//! the intervals are measured on hardware and live in the registry's quirks; for
//! an unknown family there is no default throttle, and instead one write at a
//! time with explicit user confirmation before each.
//!
//! Two things learned from prior art that the design must keep (see
//! docs/prior-art/sharkfin-methods.md):
//!
//! - a write class carries two numbers, not one: the minimum gap before the next
//!   operation, and the quiet period required *after* this write. The quiet a
//!   write needs afterwards is a property of that write;
//! - if a setting survives a power cycle, it is a flash write, however it looks
//!   in the UI. A backlight slider looks like a volatile register and is not one:
//!   a board wedged after 39 such writes a second apart, without breaching any
//!   threshold that had been reasoned out "as for a register".
//!
//! Limits live here and never in the UI, so no caller can wedge a keyboard.
//!
//! # Kill switch
//!
//! A protocol error during a write closes the session immediately. There is no
//! retry, and there is no reopen: a stalled endpoint does not recover by being
//! reopened, and further traffic keeps it stalled. All activity against that
//! device stops, including background scanning, until it is physically
//! reconnected.
