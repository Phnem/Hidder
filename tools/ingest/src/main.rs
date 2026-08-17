//! Vendor-artifact ingestion.
//!
//! Skeleton only. Built in the phase 5 epic (TICKET-18).
//!
//! Not part of the release build, and not reachable from it: this crate is a
//! separate workspace (see Cargo.toml). Do not add it to the root
//! `[workspace.members]` for convenience.
//!
//! Design decisions already made for TICKET-18, from prior art
//! (docs/prior-art/sharkfin-methods.md, method 11):
//!
//! - the generated registry must be reproducible from the vendor artifact plus a
//!   separate file of hand corrections. A vendor catalogue is not a superset of
//!   itself over time: boards get removed upstream, and some never appear;
//! - hand corrections support two kinds of entry -- a whole device, and an
//!   override of named fields on a generated device -- because most corrections
//!   are "the vendor points this board at the wrong layout", not "this board is
//!   missing";
//! - a test fails when a regeneration drops a hand correction, and a correction
//!   that upstream has since fixed is reported so it can be deleted rather than
//!   silently kept forever.

fn main() {
    println!("pingest: not implemented (TICKET-18)");
}
