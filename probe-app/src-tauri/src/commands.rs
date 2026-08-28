//! Commands exposed to the standalone Vetro Probe frontend.

use serde_json::json;
use tauri::State;

use crate::probe;

#[tauri::command]
pub fn probe_discover(probe: State<'_, probe::State>) -> Result<serde_json::Value, String> {
    probe.call("discover", json!({}))
}

#[tauri::command]
pub fn probe_plan(probe: State<'_, probe::State>) -> Result<serde_json::Value, String> {
    probe.call("plan", json!({}))
}

#[tauri::command]
pub fn probe_recovery_status(probe: State<'_, probe::State>) -> Result<serde_json::Value, String> {
    probe.call("recovery_status", json!({}))
}

#[tauri::command]
pub fn probe_start_run(probe: State<'_, probe::State>) -> Result<serde_json::Value, String> {
    probe.call("start_run", json!({}))
}

#[tauri::command]
pub fn probe_run_result(probe: State<'_, probe::State>) -> Result<serde_json::Value, String> {
    if let Ok(Some(result)) = probe.last_run_result() {
        return Ok(result);
    }
    probe.call("run_result", json!({}))
}

#[tauri::command]
pub fn probe_clear_recovery(probe: State<'_, probe::State>) -> Result<serde_json::Value, String> {
    probe.call("clear_recovery", json!({}))
}

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
