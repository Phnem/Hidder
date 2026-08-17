//! The write gate.
//!
//! # The one invariant the whole safety story rests on
//!
//! There is exactly one path from a user's intent to a byte reaching a device,
//! and it runs through this crate. Any code path that reaches an engine's write
//! logic without passing here is a blocking defect, not a style question
//! (spec.md FR2/FR3).
//!
//! ```text
//!   engine                                       what stops it
//!     |
//!     |  raw opcode                              no type expresses this
//!     X                                          and no function accepts it
//!     |
//!     |  SafeCommandId                           generated from reviewed data;
//!     v                                          destructive and unknown have
//!  SafetyGate::execute                           no variant to name
//!     |
//!     |  identified device? right family?        default deny, then the
//!     |  backed up? within the measured cadence? collision check, then backup,
//!     |  not quarantined?                        then cadence
//!     v
//!  AuthorizedCommand                             private constructor, not
//!     |                                          Clone, consumed on dispatch
//!     v
//!  CommandSink -> DeviceSession -> the wire
//! ```
//!
//! # What is actually closed, and what is not
//!
//! Closed here, by construction rather than by discipline:
//!
//! - an opcode classified `destructive` or `unknown`, or classified by nobody,
//!   has no [`SafeCommandId`] variant, so no code anywhere can name it;
//! - [`SafeCommandId::opcode`] is crate-private, so the byte is read in exactly
//!   one place: when the gate mints an [`AuthorizedCommand`] after every check
//!   has passed;
//! - [`AuthorizedCommand`] has no public constructor and is consumed on
//!   dispatch, so a sink can neither forge one nor replay the one it got;
//! - the gate owns its sink instead of lending it out, so an engine has no
//!   object to dispatch through directly.
//!
//! Not closed here, and deliberately left to TICKET-12/15: `ptransport` has no
//! write API yet, and when it grows one it must accept only what this gate
//! produced. Until then the last few centimetres of the path are held by
//! convention, and that is worth saying out loud rather than implying otherwise.
//!
//! # Rate limiting
//!
//! A property of the protocol family, not a global constant, and it comes from
//! `data/protocols/*.toml` where every number cites where it was measured. Two
//! numbers per class, not one: the minimum gap before the next operation, and
//! the quiet period required *after* this one. The second is the one prior art
//! learned expensively -- a board wedged after 39 backlight writes a second
//! apart, breaching no threshold that had been reasoned out as if the setting
//! were a register (docs/prior-art/sharkfin-methods.md).
//!
//! For a family with no measurement there is no invented interval. There is one
//! operation at a time, each with its own [`UserConfirmation`], which is taken
//! by value so that consent to one operation cannot become consent to a series.
//!
//! # Kill switch
//!
//! A stall closes everything for that device: no retry, no reopen, no
//! background polling, until it is physically reconnected. A stalled endpoint
//! does not recover from being reopened, and further traffic keeps it stalled.
//!
//! Only the typed [`ptransport::TransportError::EndpointStalled`] trips it.
//! Classifying a backend message by its text is what TICKET-08 established
//! cannot be done -- the message is the operating system's localised string --
//! so a stall the transport failed to type is a gap in the transport, not
//! something to guess at here.

pub mod class;
pub mod command;
pub mod gate;
pub mod journal;
pub mod rate;

// The generator, compiled into the crate only for its own tests. `build.rs`
// includes the same file, so the code under test is the code that runs.
#[cfg(test)]
mod codegen;

pub use class::{Burst, FamilyTiming, OpcodeClass};
pub use command::{AuthorizedCommand, SafeCommandId, known_families};
pub use gate::{BackupState, Clock, CommandSink, MonotonicClock, SafetyError, SafetyGate};
pub use journal::{
    FailureKind, Intent, JournalEntry, JournalSink, Outcome, Refusal, Verification,
    VerificationMethod,
};
pub use rate::{RateDecision, RateLimiter, UserConfirmation, WaitReason};
