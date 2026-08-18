//! Orchestration.
//!
//! Skeleton only. Wired up with TICKET-12/13.
//!
//! # Where the boundary runs
//!
//! This crate owns the model of what is connected and what state it is in. The
//! UI reads that model; the UI never reaches a device to refresh an indicator.
//! That is not tidiness. Polling from the UI competes with real work on the same
//! endpoint and adds to the write pressure that stalls it, and prior art had to
//! introduce a liveness cache specifically to stop its own frontend's polling
//! from reaching the wire (docs/prior-art/sharkfin-methods.md).
//!
//! Presentation stays out. Battery is exposed as a percentage and a charging
//! flag; rendering a tray icon from that is the application layer's job, and no
//! rasterisation dependency belongs in this crate graph
//! (architecture/INITIAL_REVIEW.md, note of 2026-08-17).
//!
//! # A capability is one thing to the layers above
//!
//! Where a value physically comes from is hidden here and below. Battery, for
//! instance, has three possible sources depending on the device -- a standard HID
//! usage, a vendor opcode, or a query to a wireless receiver rather than to the
//! device itself -- and the UI must never learn which one applied.

pub mod observe;
pub mod session;

pub use session::{CoreError, DeviceSession, DiscoveredDevice, Peripheral};
