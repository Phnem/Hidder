//! Profiles and cross-brand presets.
//!
//! Skeleton only. Designed in the phase 4 epic (TICKET-15/17).
//!
//! Decisions already fixed and not to be re-litigated here:
//!
//! - graceful degradation when applying a preset lives in this crate, once, not
//!   duplicated per engine;
//! - a hardware profile (an onboard slot) is preferred over a software profile
//!   wherever the device supports one (spec.md FR6). Prior art confirms this is
//!   also the cheaper shape if mouse protocol code is ever ported in: that code
//!   treats a profile as an entity on the device, not in the application
//!   (docs/prior-art/inventory.md);
//! - the process watcher is an opt-in thread with a whitelist. It never scans
//!   every process.
//!
//! Open reconciliation point, not to be resolved here: the plan's default was a
//! thin opt-in tray process separate from the GUI, decided before the main
//! application grew its own persistent tray icon (spec.md FR12). Whether the
//! watcher belongs in that same process is an architecture-checkpoint question
//! before TICKET-17/20.
//!
//! The profile file format is versioned from the first release. Not a v2
//! concern: without it the first community submissions become incompatible with
//! every later schema.
