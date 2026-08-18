//! Journal.
//!
//! Skeleton only. Filled alongside the first write path (TICKET-15).
//!
//! Two records, deliberately separate:
//!
//! - **What was written to a device**, so a user can see what this application
//!   did to their hardware and in what order (spec.md FR3). This is a product
//!   feature, not a debug log.
//! - **Where the knowledge about a model came from**, so the About/Journal screen
//!   can say why a control is enabled: verified on this hardware, read out of
//!   firmware, taken from vendor software, or assumed.
//!
//! # Payload rule
//!
//! Neither record stores raw payload bytes. A payload can carry a keymap, a
//! keymap can carry a macro, and a macro can carry a password. The journal is
//! meant to be readable by the user and attachable to a bug report, and those
//! two facts together mean payloads must never be in it. There is a test for
//! this, not just this paragraph (spec.md § Test seams).

pub mod log;

pub use log::JournalLog;
