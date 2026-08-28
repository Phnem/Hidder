//! Authoritative Vetro Probe Python engine sidecar bridge.

use std::collections::HashMap;
use std::fs::OpenOptions;
use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::mpsc::{channel, Receiver, RecvTimeoutError, Sender};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde::de::DeserializeOwned;
use serde_json::{json, Value};
use tauri::Manager;

pub const PROBE_PROGRESS: &str = "probe:progress";
pub const PROBE_RUN_RESULT: &str = "probe:run_result";

/// Which sidecar mode to spawn.
#[derive(Clone, Debug)]
pub enum Mode {
    Demo { scenario: String },
    Real,
}

pub type EventSink = Arc<dyn Fn(&str, Value) + Send + Sync>;

pub struct ProbeEngine {
    child: Child,
    stdin: Mutex<ChildStdin>,
    seq: AtomicU64,
    pending: Arc<Mutex<HashMap<u64, Sender<Result<Value, String>>>>>,
    last_run_result: Arc<Mutex<Option<Value>>>,
}

fn trace_diag(msg: &str) {
    let now = chrono_or_now();
    let line = format!("[{now}] [VETRO_PROBE_RPC] {msg}\n");
    print!("{line}");
    let _ = std::io::stdout().flush();
    if let Ok(mut f) = OpenOptions::new()
        .create(true)
        .append(true)
        .open("vetro_probe_rpc_diag.log")
    {
        let _ = f.write_all(line.as_bytes());
    }
}

fn chrono_or_now() -> String {
    use std::time::SystemTime;
    let dur = SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)
        .unwrap_or_default();
    format!("{}.{:03}", dur.as_secs(), dur.subsec_millis())
}

fn repo_root() -> PathBuf {
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    manifest.parent().and_then(|p| p.parent()).map(PathBuf::from).unwrap_or(manifest)
}

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

pub fn sidecar_command(mode: &Mode, root: &PathBuf) -> Command {
    let mut cmd = if let Some(bin) = find_bundled_binary(root) {
        trace_diag(&format!("SPAWN_RESOLVE: using packaged sidecar binary {:?}", bin));
        let mut c = Command::new(bin);
        c.arg("--gui-rpc");
        c
    } else {
        trace_diag("SPAWN_RESOLVE: falling back to system Python -m community.vetro_probe.cli");
        let mut c = Command::new("python");
        c.arg("-m")
            .arg("community.vetro_probe.cli")
            .arg("--gui-rpc")
            .env("PYTHONPATH", root.as_os_str());
        c
    };

    if let Mode::Demo { scenario } = mode {
        cmd.arg("--gui-demo").arg("--scenario").arg(scenario);
    }

    cmd.current_dir(root)
        .env("PYTHONIOENCODING", "utf-8")
        .env("PYTHONUTF8", "1")
        .env("PYTHONUNBUFFERED", "1")
        .env("PYTHONWARNINGS", "ignore")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x08000000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }

    cmd
}

impl ProbeEngine {
    pub fn start(mode: Mode, events: EventSink) -> Result<Self, String> {
        let root = repo_root();
        trace_diag(&format!("SPAWN_BEGIN: mode={:?} root={:?}", mode, root));
        let mut child = sidecar_command(&mode, &root)
            .spawn()
            .map_err(|e| {
                let err = format!("failed to spawn Probe engine: {e}");
                trace_diag(&format!("SPAWN_ERROR: {err}"));
                err
            })?;
        trace_diag(&format!("SPAWN_OK: child_pid={:?}", child.id()));

        let stdin = child
            .stdin
            .take()
            .ok_or_else(|| "engine stdin unavailable".to_string())?;
        trace_diag("STDIN_ACQUIRED");

        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| "engine stdout unavailable".to_string())?;
        trace_diag("STDOUT_ACQUIRED");

        let stderr = child.stderr.take();

        let engine = ProbeEngine {
            child,
            stdin: Mutex::new(stdin),
            seq: AtomicU64::new(1),
            pending: Arc::new(Mutex::new(HashMap::new())),
            last_run_result: Arc::new(Mutex::new(None)),
        };

        // 1. Drain stderr asynchronously to avoid full pipe deadlocks
        if let Some(err_pipe) = stderr {
            std::thread::spawn(move || {
                let reader = BufReader::new(err_pipe);
                for line in reader.lines() {
                    let Ok(line) = line else { break };
                    if !line.trim().is_empty() {
                        trace_diag(&format!("STDERR_LINE: {}", line.trim()));
                    }
                }
                trace_diag("STDERR_DRAIN_FINISHED");
            });
        }

        // 2. Single-reader stdout dispatcher thread
        let pending = Arc::clone(&engine.pending);
        let last = Arc::clone(&engine.last_run_result);
        std::thread::spawn(move || {
            trace_diag("STDOUT_READER_THREAD_STARTED");
            let mut reader = BufReader::new(stdout);
            let mut raw_buf = Vec::new();
            loop {
                raw_buf.clear();
                match reader.read_until(b'\n', &mut raw_buf) {
                    Ok(0) => {
                        trace_diag("STDOUT_EOF: sidecar stdout closed");
                        break;
                    }
                    Ok(n) => {
                        let line_str = String::from_utf8_lossy(&raw_buf);
                        let trimmed = line_str.trim();
                        if trimmed.is_empty() {
                            continue;
                        }
                        trace_diag(&format!("STDOUT_LINE_RECEIVED ({} bytes): {}", n, trimmed));
                        match serde_json::from_str::<Value>(trimmed) {
                            Ok(v) => {
                                trace_diag("JSON_PARSE_OK");
                                let maybe_id = v.get("id").and_then(|i| {
                                    i.as_u64()
                                        .or_else(|| i.as_i64().and_then(|n| if n >= 0 { Some(n as u64) } else { None }))
                                        .or_else(|| i.as_str().and_then(|s| s.parse::<u64>().ok()))
                                });

                                if let Some(id) = maybe_id {
                                    let ok = v.get("ok").and_then(|b| b.as_bool()).unwrap_or(false);
                                    let reply = if ok {
                                        Ok(v.get("result").cloned().unwrap_or(Value::Null))
                                    } else {
                                        Err(v.get("error")
                                            .and_then(|e| e.as_str())
                                            .unwrap_or("engine error")
                                            .to_string())
                                    };
                                    let mut guard = pending.lock().unwrap();
                                    if let Some(sender) = guard.remove(&id) {
                                        trace_diag(&format!("RESPONSE_ID_MATCH: id={} (ok={})", id, ok));
                                        let _ = sender.send(reply);
                                    } else {
                                        trace_diag(&format!("UNMATCHED_RESPONSE_ID: id={} pending_keys={:?}", id, guard.keys().collect::<Vec<_>>()));
                                    }
                                } else if let Some(name) = v.get("event").and_then(|e| e.as_str()) {
                                    let data = v.get("data").cloned().unwrap_or(Value::Null);
                                    trace_diag(&format!("EVENT_RECEIVED: name={}", name));
                                    if name == "run_result" {
                                        *last.lock().unwrap() = Some(data.clone());
                                    }
                                    events(name, data);
                                } else {
                                    trace_diag(&format!("UNRECOGNIZED_JSON_FRAME: {:?}", v));
                                }
                            }
                            Err(err) => {
                                trace_diag(&format!("JSON_PARSE_ERROR: {} on line: {}", err, trimmed));
                            }
                        }
                    }
                    Err(err) => {
                        trace_diag(&format!("STDOUT_READ_ERROR: {}", err));
                        break;
                    }
                }
            }
            // If reader breaks on EOF / error, drain pending callers with error
            let mut guard = pending.lock().unwrap();
            trace_diag(&format!("DRAINING_PENDING_ON_EXIT: count={}", guard.len()));
            for (_, tx) in guard.drain() {
                let _ = tx.send(Err("sidecar process terminated unexpectedly".to_string()));
            }
            trace_diag("STDOUT_READER_THREAD_EXITED");
        });

        Ok(engine)
    }

    pub fn call<T: DeserializeOwned>(&self, method: &str, params: Value) -> Result<T, String> {
        let t0 = Instant::now();
        let id = self.seq.fetch_add(1, Ordering::SeqCst);
        let (tx, rx): (Sender<Result<Value, String>>, Receiver<Result<Value, String>>) = channel();

        trace_diag(&format!("REQUEST_LOCK_BEGIN: id={} method={}", id, method));
        {
            let mut p = self.pending.lock().map_err(|_| "pending lock poisoned".to_string())?;
            p.insert(id, tx);
        }
        trace_diag(&format!("REQUEST_REGISTERED: id={} method={}", id, method));

        let req = json!({"id": id, "method": method, "params": params});
        let mut line = serde_json::to_string(&req).map_err(|e| e.to_string())?;
        line.push('\n');
        trace_diag(&format!("REQUEST_SERIALIZED: id={} ({} bytes)", id, line.len()));

        {
            trace_diag(&format!("STDIN_WRITE_BEGIN: id={}", id));
            let mut stdin = self.stdin.lock().map_err(|_| "stdin lock poisoned".to_string())?;
            stdin.write_all(line.as_bytes()).map_err(|e| {
                let err = format!("write to engine failed: {e}");
                trace_diag(&format!("STDIN_WRITE_ERROR: id={} err={}", id, err));
                err
            })?;
            stdin.flush().map_err(|e| {
                let err = format!("flush engine failed: {e}");
                trace_diag(&format!("STDIN_FLUSH_ERROR: id={} err={}", id, err));
                err
            })?;
            trace_diag(&format!("STDIN_FLUSH_OK: id={}", id));
        }

        trace_diag(&format!("AWAITING_RESPONSE: id={} method={}", id, method));
        let response = match rx.recv_timeout(Duration::from_secs(10)) {
            Ok(Ok(value)) => {
                let elapsed = t0.elapsed().as_millis();
                trace_diag(&format!("REQUEST_COMPLETE: id={} method={} ({} ms)", id, method, elapsed));
                serde_json::from_value(value).map_err(|e| e.to_string())
            }
            Ok(Err(err)) => {
                let elapsed = t0.elapsed().as_millis();
                trace_diag(&format!("REQUEST_FAILED: id={} method={} err={} ({} ms)", id, method, err, elapsed));
                Err(err)
            }
            Err(RecvTimeoutError::Timeout) => {
                let elapsed = t0.elapsed().as_millis();
                let _ = self.pending.lock().map(|mut p| p.remove(&id));
                let err = format!("engine call '{method}' timed out after 10s");
                trace_diag(&format!("REQUEST_TIMEOUT: id={} method={} ({} ms)", id, method, elapsed));
                Err(err)
            }
            Err(RecvTimeoutError::Disconnected) => {
                let elapsed = t0.elapsed().as_millis();
                let _ = self.pending.lock().map(|mut p| p.remove(&id));
                let err = format!("engine call '{method}' disconnected");
                trace_diag(&format!("REQUEST_DISCONNECTED: id={} method={} ({} ms)", id, method, elapsed));
                Err(err)
            }
        };

        response
    }

    pub fn last_run_result(&self) -> Option<Value> {
        self.last_run_result.lock().unwrap().clone()
    }
}

impl Drop for ProbeEngine {
    fn drop(&mut self) {
        trace_diag("PROBE_ENGINE_DROP: terminating child");
        let _ = self.child.kill();
        let _ = self.child.wait();
        trace_diag("PROBE_ENGINE_DROP_COMPLETE");
    }
}

pub struct State(pub Arc<Mutex<Option<ProbeEngine>>>);

impl State {
    pub fn call(&self, method: &str, params: Value) -> Result<Value, String> {
        let guard = self
            .0
            .lock()
            .map_err(|_| "probe engine state lock poisoned".to_string())?;
        let engine = guard
            .as_ref()
            .ok_or_else(|| "the Probe engine is not running".to_string())?;
        engine.call(method, params)
    }

    pub fn last_run_result(&self) -> Result<Option<Value>, String> {
        let guard = self
            .0
            .lock()
            .map_err(|_| "probe engine state lock poisoned".to_string())?;
        let engine = guard
            .as_ref()
            .ok_or_else(|| "the Probe engine is not running".to_string())?;
        Ok(engine.last_run_result())
    }
}

pub fn start<R: tauri::Runtime>(
    app: &tauri::AppHandle<R>,
    mode: Mode,
) -> Result<State, String> {
    let emitter = app.clone();
    let sink: EventSink = Arc::new(move |name, data| {
        let event = if name == "run_result" {
            PROBE_RUN_RESULT
        } else {
            PROBE_PROGRESS
        };
        use tauri::Emitter;
        let _ = emitter.emit(event, data);
    });
    let engine = ProbeEngine::start(mode, sink)?;
    Ok(State(Arc::new(Mutex::new(Some(engine)))))
}

pub fn replace<R: tauri::Runtime>(
    app: &tauri::AppHandle<R>,
    mode: Mode,
) -> Result<(), String> {
    let emitter = app.clone();
    let sink: EventSink = Arc::new(move |name, data| {
        let event = if name == "run_result" {
            PROBE_RUN_RESULT
        } else {
            PROBE_PROGRESS
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sidecar_command_builds_valid_probe_invocation() {
        let root = repo_root();
        let cmd = sidecar_command(&Mode::Demo { scenario: "supported".into() }, &root);
        let args: Vec<_> = cmd.get_args().map(|a| a.to_string_lossy().to_string()).collect();
        assert!(args.contains(&"--gui-rpc".to_string()));
        assert!(args.contains(&"--gui-demo".to_string()));
    }

    #[test]
    fn bundled_binary_resolution_finds_packaged_sidecar() {
        let root = repo_root();
        let bin = find_bundled_binary(&root);
        assert!(bin.is_some(), "expected to find compiled vetro-probe-sidecar.exe");
        assert!(bin.unwrap().is_file());
    }

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

        // Malformed line
        let malformed = "this is not JSON at all";
        let parsed = serde_json::from_str::<Value>(malformed);
        assert!(parsed.is_err());

        // Timeout expires safely if no valid answer arrives
        let res = rx.recv_timeout(Duration::from_millis(10));
        assert!(res.is_err());
    }

    #[test]
    fn test_production_packaged_sidecar_bridge_real_mode() {
        let root = repo_root();
        let bin = find_bundled_binary(&root);
        assert!(bin.is_some(), "vetro-probe-sidecar.exe must be present");
        let bin_path = bin.unwrap();
        println!("[DIAG] Found bundled sidecar binary: {:?}", bin_path);

        let sink: EventSink = Arc::new(|name, data| {
            println!("[DIAG EVENT] {}: {:?}", name, data);
        });

        let start_time = Instant::now();
        let engine = ProbeEngine::start(Mode::Real, sink).expect("ProbeEngine::start failed");
        let spawn_ms = start_time.elapsed().as_millis();
        println!("[DIAG] Engine spawned in {} ms", spawn_ms);

        // 1. Health
        let t0 = Instant::now();
        let health: Value = engine.call("health", json!({})).expect("health call failed");
        let health_ms = t0.elapsed().as_millis();
        println!("[DIAG] health ({} ms): {:?}", health_ms, health);

        // 2. Recovery Status
        let t1 = Instant::now();
        let rec: Value = engine.call("recovery_status", json!({})).expect("recovery_status call failed");
        let rec_ms = t1.elapsed().as_millis();
        println!("[DIAG] recovery_status ({} ms): {:?}", rec_ms, rec);

        // 3. Discover
        let t2 = Instant::now();
        let disc: Value = engine.call("discover", json!({})).expect("discover call failed");
        let disc_ms = t2.elapsed().as_millis();
        println!("[DIAG] discover ({} ms): {:?}", disc_ms, disc);

        // 4. Plan
        let t3 = Instant::now();
        let plan: Value = engine.call("plan", json!({})).expect("plan call failed");
        let plan_ms = t3.elapsed().as_millis();
        println!("[DIAG] plan ({} ms): {:?}", plan_ms, plan);

        assert_eq!(health["method"], "health");
        assert!(rec.get("preflight").is_some());
        assert!(disc.get("state").is_some());
        assert!(plan.get("safe").is_some());
        assert_eq!(plan["safe_count"], 6);
    }
}
