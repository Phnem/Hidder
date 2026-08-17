//! Mechanism 1: commands. Request/response, always UI-initiated.
//!
//! For operations with a result the caller waits on: read a configuration,
//! change a setting, connect to a device. Not for streams, and not for anything
//! the backend needs to announce on its own.
//!
//! Every command that can fail returns a `Result` with a message fit to show a
//! user. "Protocol error 5" is not such a message; "unplug it, wait ten seconds,
//! plug it back in" is.

use serde::Serialize;

/// A device as the UI needs to see it.
///
/// Shape is provisional (TICKET-12/13 fill it). Two fields are here from the
/// start because they are contract, not detail:
///
/// - `read_only` -- a device whose protocol family is not verified opens
///   read-only, and the UI must know that without having to infer it from an
///   empty capability set (spec.md § Failure and fallback behavior);
/// - `label` -- a device always has something a human can read, even when it is
///   not in the registry at all.
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DeviceView {
    pub id: u64,
    pub label: String,
    pub read_only: bool,
}

/// Version plus commit of the running build, for the About screen and bug
/// reports.
#[tauri::command]
pub fn build_id() -> String {
    crate::build_id()
}

/// Devices currently known to `pcore`.
///
/// Returns the orchestrator's model, and never touches hardware itself: see the
/// note about UI polling in [`super`].
#[tauri::command]
pub fn list_devices() -> Vec<DeviceView> {
    // TICKET-12 wires this to pcore. An empty list is the honest answer while
    // there is no transport, and it is also the correct answer when nothing is
    // plugged in, so no caller has to special-case a skeleton.
    Vec::new()
}
