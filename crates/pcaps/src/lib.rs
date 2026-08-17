//! Capability vocabulary.
//!
//! Skeleton only. Real types arrive with TICKET-10/11/12; this crate exists now
//! so the dependency direction is fixed before anything is written against it.
//!
//! # Why this crate is the one to get right
//!
//! Every other layer depends on it, and changing a capability's type or unit
//! after protocol engines are written against it is expensive
//! (architecture/INITIAL_REVIEW.md §3). Two constraints are already known and
//! must not be designed away:
//!
//! - the value shape must cover scalar, struct (DKS, SOCD), matrix (keymap) and
//!   *stream* (analog travel) from the start, because retrofitting a stream onto
//!   a scalar-only enum breaks every engine at once;
//! - a capability carries where the knowledge came from, and "the device
//!   answered" is not one of the trustworthy origins.
//!
//! # The origin marker
//!
//! A device's answer is not proof: an unsupported command often replays the
//! previous reply or returns plausible fiction. Silence is not proof either --
//! an opcode that goes unanswered on one board proves only that this firmware
//! on this board does not implement it (spec.md § Domain rules). So `Unsupported`
//! needs the same evidence as `Verified(hw)`, and neither is inferred.
//!
//! Open question for the architecture checkpoint before phase 3: prior art shows
//! capabilities that can only be known from the registry, because the firmware
//! answers the query whether or not the hardware exists (a board with no edge
//! light still answers the edge-light read). That is a fact about the product,
//! not about the protocol, and today's origin scale has no slot for it. Do not
//! quietly map it onto `Assumed` -- see docs/prior-art/sharkfin-methods.md.
