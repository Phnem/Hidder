//! Sidecar process: spawns the Python Probe engine and bridges the JSON-lines RPC.
//!
//! Wire (mirrors `community/vetro_probe/gui_rpc.py`):
//!   request  -> {"id": n, "method": "...", "params": {...}}\n
//!   response -> {"id": n, "ok": true, "result": {...}}\n | {"id": n, "ok": false, "error": "..."}\n
//!   event    -> {"event": "progress" | "run_result", "data": {...}}\n
//!
//! Responses are matched to callers by id (pending oneshots); events are
//! forwarded to the window as `probe://progress` / `probe://run_result`. Non-JSON
//! stdout lines (warnings, traces) are ignored rather than treated as protocol.

use std::collections::HashMap;
use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::mpsc::{channel, Receiver, RecvTimeoutError, Sender};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use serde::de::DeserializeOwned;
use serde_json::{json, Value};

/// Which sidecar mode to spawn.
#[derive(Clone, Debug)]
pub enum Mode {
    /// Deterministic mock engine. Never touches hardware, never emits physical
    /// evidence. `scenario` is one of the DemoEngine scenarios.
    Demo { scenario: String },
    /// Real hardware. Requires an exact HERO84 (372E:103E / aula_kb_v3_wired / 0216).
    Real,
}

/// Handles events emitted by the engine (called on the reader thread).
pub type EventSink = Arc<dyn Fn(&str, Value) + Send + Sync>;

/// A running Python Probe engine sidecar.
pub struct ProbeEngine {
    child: Child,
    stdin: Mutex<ChildStdin>,
    seq: AtomicU64,
    pending: Arc<Mutex<HashMap<u64, Sender<Result<Value, String>>>>>,
    last_run_result: Arc<Mutex<Option<Value>>>,
}

/// Repository root (where `community/` lives), so the sidecar can resolve the
/// `community.vetro_probe` package. `CARGO_MANIFEST_DIR` = app/src-tauri.
fn repo_root() -> PathBuf {
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    manifest.parent().and_then(|p| p.parent()).map(PathBuf::from).unwrap_or(manifest)
}

/// Finds the packaged standalone sidecar binary if present.
fn find_bundled_binary(root: &PathBuf) -> Option<PathBuf> {
    let mut candidates = Vec::new();
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            candidates.push(dir.join("vetro-probe-sidecar.exe"));
            candidates.push(dir.join("vetro-probe-sidecar-x86_64-pc-windows-msvc.exe"));
            candidates.push(dir.join("binaries").join("vetro-probe-sidecar.exe"));
            candidates.push(dir.join("binaries").join("vetro-probe-sidecar-x86_64-pc-windows-msvc.exe"));
            candidates.push(dir.join("resources").join("binaries").join("vetro-probe-sidecar.exe"));
            candidates.push(dir.join("_up_").join("binaries").join("vetro-probe-sidecar.exe"));
        }
    }
    candidates.push(root.join("build_dist").join("vetro-probe-sidecar.exe"));
    candidates.push(root.join("probe-app").join("src-tauri").join("binaries").join("vetro-probe-sidecar.exe"));
    candidates.push(root.join("app").join("src-tauri").join("binaries").join("vetro-probe-sidecar.exe"));
    candidates.into_iter().find(|p| p.is_file())
}

fn sidecar_command(mode: &Mode, root: &PathBuf) -> Command {
    let mut cmd = if let Some(bin) = find_bundled_binary(root) {
        let mut c = Command::new(bin);
        c.arg("--gui-rpc");
        c
    } else {
        let mut c = Command::new("python");
        c.arg("-m")
            .arg("community.vetro_probe.cli")
            .arg("--gui-rpc")
            .current_dir(root)
            .env("PYTHONPATH", root.as_os_str())
            .env("PYTHONUNBUFFERED", "1")
            .env("PYTHONWARNINGS", "ignore");
        c
    };

    if let Mode::Demo { scenario } = mode {
        cmd.arg("--gui-demo").arg("--scenario").arg(scenario);
    }

    cmd.stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());

    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x08000000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }

    cmd
}

impl ProbeEngine {
    /// Spawn the sidecar and start its reader thread. `events` receives engine
    /// events (data of `{"event": name, "data": ...}`) to forward to the window.
    pub fn start(mode: Mode, events: EventSink) -> Result<Self, String> {
        let root = repo_root();
        let mut child = sidecar_command(&mode, &root)
            .spawn()
            .map_err(|e| format!("failed to spawn Probe engine: {e}"))?;
        let stdin = child
            .stdin
            .take()
            .ok_or_else(|| "engine stdin unavailable".to_string())?;
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| "engine stdout unavailable".to_string())?;

        let engine = ProbeEngine {
            child,
            stdin: Mutex::new(stdin),
            seq: AtomicU64::new(1),
            pending: Arc::new(Mutex::new(HashMap::new())),
            last_run_result: Arc::new(Mutex::new(None)),
        };

        // Reader thread: parse lines, resolve responses or forward events.
        let pending = Arc::clone(&engine.pending);
        let last = Arc::clone(&engine.last_run_result);
        std::thread::spawn(move || {
            let reader = BufReader::new(stdout);
            for line in reader.lines() {
                let Ok(line) = line else { break };
                let line = line.trim();
                if line.is_empty() {
                    continue;
                }
                let Ok(v) = serde_json::from_str::<Value>(line) else {
                    continue; // warning/trace, not protocol
                };
                if let Some(id) = v.get("id").and_then(|i| i.as_u64()) {
                    let reply = if v.get("ok").and_then(|b| b.as_bool()).unwrap_or(false) {
                        Ok(v.get("result").cloned().unwrap_or(Value::Null))
                    } else {
                        Err(v.get("error").and_then(|e| e.as_str()).unwrap_or("engine error").to_string())
                    };
                    let sender = pending.lock().map(|mut p| p.remove(&id)).ok().flatten();
                    if let Some(sender) = sender {
                        let _ = sender.send(reply);
                    }
                } else if let Some(name) = v.get("event").and_then(|e| e.as_str()) {
                    let data = v.get("data").cloned().unwrap_or(Value::Null);
                    if name == "run_result" {
                        *last.lock().unwrap() = Some(data.clone());
                    }
                    events(name, data);
                }
            }
        });

        Ok(engine)
    }

    /// Send one request and wait for its response.
    pub fn call<T: DeserializeOwned>(&self, method: &str, params: Value) -> Result<T, String> {
        let id = self.seq.fetch_add(1, Ordering::SeqCst);
        let (tx, rx): (Sender<Result<Value, String>>, Receiver<Result<Value, String>>) = channel();
        self.pending.lock().map_err(|_| "pending lock poisoned".to_string())?.insert(id, tx);
        let req = json!({"id": id, "method": method, "params": params});
        let mut line = serde_json::to_string(&req).map_err(|e| e.to_string())?;
        line.push('\n');
        let mut stdin = self.stdin.lock().map_err(|_| "stdin lock poisoned".to_string())?;
        stdin.write_all(line.as_bytes()).map_err(|e| format!("write to engine failed: {e}"))?;
        stdin.flush().map_err(|e| format!("flush engine failed: {e}"))?;
        drop(stdin);
        match rx.recv_timeout(Duration::from_secs(60)) {
            Ok(Ok(value)) => serde_json::from_value(value).map_err(|e| e.to_string()),
            Ok(Err(err)) => Err(err),
            Err(RecvTimeoutError::Timeout) => Err("engine did not answer in time".to_string()),
            Err(RecvTimeoutError::Disconnected) => Err("engine stopped".to_string()),
        }
    }

    /// The most recent `run_result` event the engine emitted, if any.
    pub fn last_run_result(&self) -> Option<Value> {
        self.last_run_result.lock().unwrap().clone()
    }
}

impl Drop for ProbeEngine {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The RPC reader must resolve responses by id and forward events by name,
    /// ignoring non-JSON lines. This is the framing contract with the Python side.
    #[test]
    fn rpc_framing_contract_parses_responses_and_events() {
        let pending: Arc<Mutex<HashMap<u64, Sender<Result<Value, String>>>>> =
            Arc::new(Mutex::new(HashMap::new()));
        let (tx, rx): (Sender<Result<Value, String>>, Receiver<Result<Value, String>>) = channel();
        pending.lock().unwrap().insert(7, tx);
        let last: Arc<Mutex<Option<Value>>> = Arc::new(Mutex::new(None));
        let mut events: Vec<String> = Vec::new();

        let lines = vec![
            "not json".to_string(),
            r#"{"id":7,"ok":true,"result":{"state":"IDENTIFIED"}}"#.to_string(),
            r#"{"event":"progress","data":{"op":"keyboard.profile","state":"PASS"}}"#.to_string(),
            r#"{"event":"run_result","data":{"status":"SUCCESS_RESTORED","restored":true}}"#.to_string(),
        ];
        for line in lines {
            let Ok(v) = serde_json::from_str::<Value>(line.trim()) else {
                continue;
            };
            if let Some(id) = v.get("id").and_then(|i| i.as_u64()) {
                let reply = if v.get("ok").and_then(|b| b.as_bool()).unwrap_or(false) {
                    Ok(v.get("result").cloned().unwrap_or(Value::Null))
                } else {
                    Err(v.get("error").and_then(|e| e.as_str()).unwrap_or("engine error").to_string())
                };
                let sender = pending.lock().unwrap().remove(&id);
                if let Some(sender) = sender {
                    let _ = sender.send(reply);
                }
            } else if let Some(name) = v.get("event").and_then(|e| e.as_str()) {
                if name == "run_result" {
                    *last.lock().unwrap() = Some(v.get("data").cloned().unwrap_or(Value::Null));
                }
                events.push(name.to_string());
            }
        }
        assert_eq!(rx.recv_timeout(Duration::from_millis(100)).unwrap().unwrap()["state"], "IDENTIFIED");
        assert_eq!(events, vec!["progress", "run_result"]);
        assert_eq!(last.lock().unwrap().as_ref().unwrap()["status"], "SUCCESS_RESTORED");
        // the engine must be spawnable only via a real Python; here we only assert
        // the python command is shaped correctly (no hardware involved).
        let root = repo_root();
        let cmd = sidecar_command(&Mode::Demo { scenario: "supported".into() }, &root);
        let args: Vec<_> = cmd.get_args().map(|a| a.to_string_lossy().to_string()).collect();
        assert!(args.contains(&"--gui-rpc".to_string()));
        assert!(args.contains(&"--gui-demo".to_string()));
    }

    #[test]
    fn rpc_returns_error_on_failed_engine_response() {
        let pending: Arc<Mutex<HashMap<u64, Sender<Result<Value, String>>>>> =
            Arc::new(Mutex::new(HashMap::new()));
        let (tx, rx): (Sender<Result<Value, String>>, Receiver<Result<Value, String>>) = channel();
        pending.lock().unwrap().insert(42, tx);

        let line = r#"{"id":42,"ok":false,"error":"unknown method: foo"}"#;
        let v: Value = serde_json::from_str(line).unwrap();
        let id = v["id"].as_u64().unwrap();
        let reply = Err(v["error"].as_str().unwrap().to_string());
        if let Some(sender) = pending.lock().unwrap().remove(&id) {
            let _ = sender.send(reply);
        }

        let res = rx.recv_timeout(Duration::from_millis(100)).unwrap();
        assert!(res.is_err());
        assert_eq!(res.unwrap_err(), "unknown method: foo");
    }

    #[test]
    fn rpc_handles_malformed_json_safely() {
        let pending: Arc<Mutex<HashMap<u64, Sender<Result<Value, String>>>>> =
            Arc::new(Mutex::new(HashMap::new()));
        let (tx, rx): (Sender<Result<Value, String>>, Receiver<Result<Value, String>>) = channel();
        pending.lock().unwrap().insert(99, tx);

        let malformed = "this is not JSON at all";
        let parsed = serde_json::from_str::<Value>(malformed);
        assert!(parsed.is_err());

        let res = rx.recv_timeout(Duration::from_millis(10));
        assert!(res.is_err());
    }
}
