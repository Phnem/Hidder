//! The IPC contract between the UI and `pcore`.
//!
//! Three mechanisms, chosen per traffic shape, laid down here in the skeleton
//! (spec.md FR11, architecture/INITIAL_REVIEW.md §6). This split is not an
//! optimisation to apply later: retrofitting an ordered stream onto code already
//! wired through the general event bus is a breaking change to the contract, not
//! an addition.
//!
//! | Mechanism | Traffic | Module |
//! |---|---|---|
//! | Commands | request/response, UI-initiated | [`commands`] |
//! | Events | rare notifications, backend-initiated | [`events`] |
//! | Channels | ordered high-throughput streams | [`channels`] |
//!
//! # Why channels are mandatory rather than preferred
//!
//! Tauri documents that async event listeners may process events out of order.
//! For an analog travel waveform (up to 84 keys at 100-1000 Hz) reordering is
//! not a cosmetic problem: the waveform is the evidence a write took effect
//! (spec.md FR1/FR11). Any stream code that subscribes through the general event
//! listener API instead of a channel is a blocking review finding.
//!
//! # What must not appear here
//!
//! The UI never asks a device for anything in order to refresh an indicator.
//! Device state is owned by `pcore` and pushed up through events; UI polling
//! competes with real work on the same endpoint and adds to the write pressure
//! that stalls it. This is the failure mode prior art had to add a liveness
//! cache to work around (docs/prior-art/sharkfin-methods.md).

pub mod channels;
pub mod commands;
pub mod events;

use tauri::Manager;

/// Bring the running instance forward when a second launch is attempted.
///
/// A second instance must never reach a device: two copies polling one keyboard
/// is enough sustained traffic to stall its config endpoint.
pub fn raise_existing_window<R: tauri::Runtime>(app: &tauri::AppHandle<R>) {
    if let Some(window) = app.webview_windows().values().next() {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
    }
}
