//! Device emulator.
//!
//! Skeleton only. Implemented in TICKET-14.
//!
//! The point of this tool is not to be a HID echo server. It is the only way to
//! have CI without physical hardware on the runner, and it is only useful if it
//! reproduces the specific ways real firmware misleads a host:
//!
//! - replaying the previous reply when sent an unsupported opcode, so the
//!   anti-fiction filter has something to catch;
//! - answering a query for hardware that is not physically present;
//! - stalling its endpoint when written to faster than a measured rate, and
//!   staying stalled until "reconnected".
//!
//! An emulator that only answers correctly proves nothing about the code paths
//! that matter.

fn main() {
    println!("pemu: not implemented (TICKET-14)");
}
