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
//! # There are two doors, and the second one is narrower
//!
//! The path above is for commands that have been verified. TICKET-12 added a
//! second, for the one case that path cannot serve: the first send of a command
//! nobody here has ever sent. A `safe_read` is earned with hardware evidence,
//! and hardware evidence is earned by sending the command once, so without
//! somewhere for that first send to live the rule is unsatisfiable and the
//! pressure is to put a hole in it.
//!
//! ```text
//!   ProbeCommandId                  a different generated enum; only
//!     |                             `bootstrap_probe` entries appear in it
//!     v
//!  ProbeGate::probe(self, ..)       consumes the gate, so one authorisation is
//!     |                             one send and a series cannot be expressed
//!     |  UserConfirmation           by value, one person, one question
//!     v
//!  AuthorizedProbe                  its own type: a ProbeSink cannot be handed
//!     |                             a production command and vice versa
//!     v
//!  ProbeSink -> the wire -> ProbeResponse::decode -> a typed value or an error
//! ```
//!
//! No raw bytes enter or leave that path, there is no retry loop in it, and
//! nothing on it writes to the ACL. Promoting a probed command to a
//! [`SafeCommandId`] is a person editing `data/protocols/*.toml` and rebuilding
//! -- see [`probe`] for why each of those is the way it is.
//!
//! # What is actually closed, and what is not
//!
//! Closed here, by construction rather than by discipline:
//!
//! - a command classified `destructive` or `unknown`, or classified by nobody,
//!   has no [`SafeCommandId`] and no [`ProbeCommandId`] variant, so no code
//!   anywhere can name it;
//! - `SafeCommandId::key` is crate-private, so the bytes that identify a
//!   command are read in exactly one place: when the gate mints an
//!   [`AuthorizedCommand`] after every check has passed;
//! - that identity is a [`CommandKey`] and not a byte, because one family
//!   addresses commands as a group plus a subcommand, and authorising the group
//!   would authorise every subcommand in it -- including the ones nobody has
//!   classified;
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
pub mod key;
pub mod probe;
pub mod rate;

// The generator, compiled into the crate only for its own tests. `build.rs`
// includes the same file, so the code under test is the code that runs.
#[cfg(test)]
mod codegen;

pub use class::{Burst, FamilyTiming, OpcodeClass};
pub use command::{AuthorizedCommand, SafeCommandId, known_families};
pub use gate::{
    BackupState, Clock, CommandResponse, CommandSink, MonotonicClock, ReadError, SafetyError,
    SafetyGate,
};
pub use journal::{
    FailureKind, Intent, JournalEntry, JournalSink, Outcome, Refusal, Verification,
    VerificationMethod,
};
pub use key::{CommandKey, HeaderBytes};
pub use probe::{AuthorizedProbe, ProbeCommandId, ProbeError, ProbeGate, ProbeResponse, ProbeSink};
pub use rate::{RateDecision, RateLimiter, UserConfirmation, WaitReason};
