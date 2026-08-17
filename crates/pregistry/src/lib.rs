//! Device registry and fingerprinting.
//!
//! Skeleton only. Real matching arrives with TICKET-10.
//!
//! # What this crate hides
//!
//! Signal scoring. Callers ask "what device is this, and how sure are we" and
//! get one answer back, never a vector of raw signals to weigh themselves.
//!
//! # The rule that shapes everything here
//!
//! A protocol family is never derived from VID:PID. Vendors reuse product ids,
//! and one OEM's boards ship under several vendor ids. Matching is
//! multi-signal with explicit confidence, weakest signal last: report
//! descriptor hash, then the set of top-level collections, then
//! manufacturer/product strings, then a safe identify opcode, then firmware
//! version, and only then VID:PID as an index rather than an answer.
//!
//! Independent confirmation from prior art covering 949 boards: family is
//! resolved from the registry, never from the product id (docs/prior-art/royuan.md).
//!
//! Concrete first signals for the ROYUAN family, already known and not to be
//! rediscovered: identify returns a registry id as a little-endian `u32`, and
//! firmware revision comes back split across two reply bytes. Both are reads,
//! both are on the confirmed-answering list.
//!
//! # Two invariants for CI, not for a comment
//!
//! - Every device in the registry must be reachable by discovery. A board whose
//!   vendor id is never scanned for is listed as supported and can never be
//!   found, which reads to its owner as the app being broken.
//! - The registry is generated from reviewable sources (spec.md FR4), and hand
//!   corrections live beside the generated output rather than inside it, with a
//!   test that fails when a regeneration drops one. A vendor catalogue is not a
//!   superset of itself over time: entries get removed upstream
//!   (docs/prior-art/sharkfin-methods.md, method 11).
//!
//! Open question for the architecture checkpoint before phase 3: prior art
//! grants a capability per *firmware lineage*, not per family, because boards
//! in one family parse the same request differently, and a shared family is not
//! evidence. Our schema has family plus confidence and no lineage level between
//! family and board. Do not settle this before the first engine exists.
