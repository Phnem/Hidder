//! Platform escape hatches.
//!
//! Reserved by TICKET-07, empty on purpose. `hidapi` is the primary transport
//! (spec.md FR7); a module here exists only for a capability `hidapi` does not
//! expose, and whether any is needed is an open empirical question answered by
//! TICKET-08/09, not an assumption to build on now.
//!
//! Nothing in here may appear in a public signature of this crate. WinRT
//! `Windows.Devices.HumanInterfaceDevice` is not used in any form.

#[cfg(target_os = "windows")]
pub mod windows;

#[cfg(target_os = "linux")]
pub mod linux;

#[cfg(target_os = "macos")]
pub mod macos;
