//! Bridge to the authoritative Vetro Probe Python engine.
//!
//! The desktop shell does NOT contain the executor, planner, safety gate,
//! recovery or protocol code. It spawns `python -m community.vetro_probe.cli
//! --gui-rpc [--gui-demo ...]` as a sidecar child process and speaks the
//! JSON-lines RPC defined by `community/vetro_probe/gui_rpc.py`. This layer is
//! thin on purpose: requests go to the Python engine; events come back and are
//! forwarded to the window. Mock/demo mode is deterministic and never touches
//! hardware.

pub mod sidecar;

use std::sync::{Arc, Mutex};

use serde_json::Value;
use tauri::Manager;

pub use sidecar::{Mode, ProbeEngine};

/// Tauri-managed handle on the running engine. `None` means the engine is not
/// up (e.g. Python missing); every command then reports a clear sentence.
pub struct State(pub Arc<Mutex<Option<ProbeEngine>>>);

impl State {
    pub fn call(&self, method: &str, params: Value) -> Result<Value, String> {
        let guard = self
            .0
            .lock()
            .map_err(|_| "probe engine state lock poisoned".to_string())?;
        let engine = guard
            .as_ref()
            .ok_or_else(|| "the Probe engine is not running (is Python on PATH?)".to_string())?;
        engine.call(method, params)
    }

    pub fn last_run_result(&self) -> Result<Option<Value>, String> {
        let guard = self
            .0
            .lock()
            .map_err(|_| "probe engine state lock poisoned".to_string())?;
        let engine = guard
            .as_ref()
            .ok_or_else(|| "the Probe engine is not running (is Python on PATH?)".to_string())?;
        Ok(engine.last_run_result())
    }
}

/// Spawn the engine and return its State, forwarding engine events to the window
/// as `probe:progress` / `probe:run_result`.
pub fn start<R: tauri::Runtime>(
    app: &tauri::AppHandle<R>,
    mode: Mode,
) -> Result<State, String> {
    let emitter = app.clone();
    let sink: sidecar::EventSink = Arc::new(move |name, data| {
        let event = if name == "run_result" {
            crate::ipc::events::PROBE_RUN_RESULT
        } else {
            crate::ipc::events::PROBE_PROGRESS
        };
        use tauri::Emitter;
        let _ = emitter.emit(event, data);
    });
    let engine = ProbeEngine::start(mode, sink)?;
    Ok(State(Arc::new(Mutex::new(Some(engine)))))
}

/// Swap the running engine (demo <-> real). Used by `probe_set_mode`.
pub fn replace<R: tauri::Runtime>(
    app: &tauri::AppHandle<R>,
    mode: Mode,
) -> Result<(), String> {
    let emitter = app.clone();
    let sink: sidecar::EventSink = Arc::new(move |name, data| {
        let event = if name == "run_result" {
            crate::ipc::events::PROBE_RUN_RESULT
        } else {
            crate::ipc::events::PROBE_PROGRESS
        };
        use tauri::Emitter;
        let _ = emitter.emit(event, data);
    });
    let engine = ProbeEngine::start(mode, sink)?;
    let current = app.state::<State>();
    let mut guard = current
        .0
        .lock()
        .map_err(|_| "probe engine state lock poisoned".to_string())?;
    *guard = Some(engine);
    Ok(())
}
