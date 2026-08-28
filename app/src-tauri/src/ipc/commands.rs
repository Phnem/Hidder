//! Mechanism 1: commands. Request/response, always UI-initiated.
//!
//! For operations with a result the caller waits on: list what is connected,
//! open a session, read a capability, fetch the journal. Not for streams, and
//! not for anything the backend needs to announce on its own.
//!
//! # These are thin on purpose
//!
//! Every function here does the same three things: take the shared handle, ask
//! `pcore`, turn a failure into a sentence. None of them decides anything about
//! a device, because deciding things about devices is what the crate below is
//! for, and a rule enforced in two places is a rule that will eventually be
//! enforced differently in each.
//!
//! # Errors are written for a person
//!
//! Every command that can fail returns a `Result` with a message fit to show a
//! user. "Protocol error 5" is not such a message; "unplug it, wait ten seconds,
//! plug it back in" is. The wording comes from `pcore`, so the same failure
//! reads the same way in the window and in the journal.

use pcaps::CapId;
use pcore::{CapabilityReading, DeviceCard, JournalRow};
use serde_json::json;
use tauri::State;

use crate::ipc::Devices;
use crate::probe;

/// Command names, declared once.
///
/// The frontend spells these too, in `app/src/ipc.ts`, and a typo on either side
/// is silent: `invoke` rejects at runtime with a message nobody sees until a
/// screen is empty. `super::tests` reads the TypeScript file and checks that
/// every name here appears in it, which is why they are constants rather than
/// only attribute arguments.
pub const BUILD_ID: &str = "build_id";
pub const LIST_DEVICES: &str = "list_devices";
pub const CONNECT_DEVICE: &str = "connect_device";
pub const DISCONNECT_DEVICE: &str = "disconnect_device";
pub const READ_CAPABILITY: &str = "read_capability";
pub const JOURNAL: &str = "journal";
// Vetro Probe research flow (thin bridge to the Python engine sidecar).
pub const PROBE_DISCOVER: &str = "probe_discover";
pub const PROBE_PLAN: &str = "probe_plan";
pub const PROBE_RECOVERY_STATUS: &str = "probe_recovery_status";
pub const PROBE_START_RUN: &str = "probe_start_run";
pub const PROBE_RUN_RESULT: &str = "probe_run_result";
pub const PROBE_CLEAR_RECOVERY: &str = "probe_clear_recovery";
pub const PROBE_SET_MODE: &str = "probe_set_mode";
pub const PROBE_OPEN_RESULTS: &str = "probe_open_results";

pub const ALL: &[&str] = &[
    BUILD_ID,
    LIST_DEVICES,
    CONNECT_DEVICE,
    DISCONNECT_DEVICE,
    READ_CAPABILITY,
    JOURNAL,
    PROBE_DISCOVER,
    PROBE_PLAN,
    PROBE_RECOVERY_STATUS,
    PROBE_START_RUN,
    PROBE_RUN_RESULT,
    PROBE_CLEAR_RECOVERY,
    PROBE_SET_MODE,
    PROBE_OPEN_RESULTS,
    super::channels::SUBSCRIBE_ANALOG_STREAM,
];

/// Version plus commit of the running build, for the About screen and bug
/// reports.
#[tauri::command]
pub fn build_id() -> String {
    crate::build_id()
}

/// Devices currently known to `pcore`.
///
/// Returns the orchestrator's model, and never touches hardware itself: see the
/// note about UI polling in [`super`]. An empty list is the correct answer when
/// nothing is plugged in, so no caller has to special-case it.
#[tauri::command]
pub fn list_devices(devices: State<'_, Devices>) -> Result<Vec<DeviceCard>, String> {
    devices
        .service()
        .devices()
        .map_err(|error| error.to_string())
}

/// Opens a session with one device.
///
/// This is the first thing in the application that sends a device anything, and
/// it happens because a person asked for it. Nothing connects on its own: the
/// vendor's software may be holding the endpoint, and the policy is to detect
/// that and say so rather than race for it.
#[tauri::command]
pub fn connect_device(id: u64, devices: State<'_, Devices>) -> Result<DeviceCard, String> {
    flatten(devices.service().connect(id))
}

/// Ends a session. The device stays listed; it is simply no longer open.
#[tauri::command]
pub fn disconnect_device(id: u64, devices: State<'_, Devices>) -> Result<DeviceCard, String> {
    flatten(devices.service().disconnect(id))
}

/// Reads one capability by its canonical id.
///
/// The id arrives as a string and is resolved against the closed vocabulary
/// before anything else happens, so a frontend cannot ask for a capability that
/// does not exist -- it gets a refusal naming what it asked for, rather than an
/// empty panel.
#[tauri::command]
pub fn read_capability(
    id: u64,
    capability: String,
    devices: State<'_, Devices>,
) -> Result<CapabilityReading, String> {
    let Some(capability) = CapId::all().iter().find(|c| c.as_str() == capability) else {
        return Err(format!("no such capability: {capability}"));
    };
    flatten(devices.service().read(id, *capability))
}

/// Everything the safety gate has authorised in this process, oldest first.
///
/// Includes refusals and failures. A journal of successes could not answer the
/// question it exists for -- what did this program do to my keyboard before it
/// stopped working.
#[tauri::command]
pub fn journal(devices: State<'_, Devices>) -> Result<Vec<JournalRow>, String> {
    devices
        .service()
        .journal()
        .map_err(|error| error.to_string())
}

// --- Vetro Probe research flow (thin; the Python engine is authoritative) ----

/// Current device discovery state (NO_DEVICE / IDENTIFIED / FW_UNSUPPORTED ...).
#[tauri::command]
pub fn probe_discover(probe: State<'_, probe::State>) -> Result<serde_json::Value, String> {
    probe.call("discover", json!({}))
}

/// Plan preview straight from the backend planner (never hardcoded in the UI).
#[tauri::command]
pub fn probe_plan(probe: State<'_, probe::State>) -> Result<serde_json::Value, String> {
    probe.call("plan", json!({}))
}

/// Recovery-first preflight (CLEAR / RECOVERING / ...).
#[tauri::command]
pub fn probe_recovery_status(probe: State<'_, probe::State>) -> Result<serde_json::Value, String> {
    probe.call("recovery_status", json!({}))
}

/// Start the research run; progress/run_result arrive as `probe:progress` events.
#[tauri::command]
pub fn probe_start_run(probe: State<'_, probe::State>) -> Result<serde_json::Value, String> {
    probe.call("start_run", json!({}))
}

/// The latest run result (also delivered as a `probe:run_result` event).
#[tauri::command]
pub fn probe_run_result(probe: State<'_, probe::State>) -> Result<serde_json::Value, String> {
    if let Ok(Some(result)) = probe.last_run_result() {
        return Ok(result);
    }
    probe.call("run_result", json!({}))
}

/// Clear a pending (demo) recovery after the user restored the device manually.
#[tauri::command]
pub fn probe_clear_recovery(probe: State<'_, probe::State>) -> Result<serde_json::Value, String> {
    probe.call("clear_recovery", json!({}))
}

/// Switch the engine between deterministic demo and real hardware.
#[tauri::command]
pub fn probe_set_mode(
    mode: String,
    scenario: String,
    app: tauri::AppHandle,
) -> Result<serde_json::Value, String> {
    let mode = if mode == "real" {
        probe::Mode::Real
    } else {
        probe::Mode::Demo { scenario }
    };
    probe::replace(&app, mode)?;
    Ok(json!({"ok": true}))
}

/// Open the directory containing research results and journals in the file explorer.
#[tauri::command]
pub fn probe_open_results() -> Result<(), String> {
    let cwd = std::env::current_dir().map_err(|e| e.to_string())?;
    #[cfg(windows)]
    {
        let zip_path = cwd.join("vetro_probe_results.zip");
        let run_dir = cwd.join("vetro_gui_run");
        if zip_path.exists() {
            let _ = std::process::Command::new("explorer")
                .arg(format!("/select,{}", zip_path.display()))
                .spawn();
        } else if run_dir.exists() {
            let _ = std::process::Command::new("explorer").arg(&run_dir).spawn();
        } else {
            let _ = std::process::Command::new("explorer").arg(&cwd).spawn();
        }
    }
    Ok(())
}

/// Collapses "the service is gone" and "the device said no" into one message.
///
/// They are different failures and `pcore` keeps them apart, which is right one
/// layer down. At this boundary both mean the same thing to the person looking
/// at the screen: this did not happen, and here is why.
fn flatten<T, E: std::fmt::Display>(
    result: Result<Result<T, E>, pcore::ServiceStopped>,
) -> Result<T, String> {
    match result {
        Ok(Ok(value)) => Ok(value),
        Ok(Err(error)) => Err(error.to_string()),
        Err(stopped) => Err(stopped.to_string()),
    }
}
