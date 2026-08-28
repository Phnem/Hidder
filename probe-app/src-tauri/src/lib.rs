pub mod commands;
pub mod probe;

use tauri::Manager;

pub fn run() {
    // Check command-line args for --demo
    let args: Vec<String> = std::env::args().collect();
    let is_demo = args.iter().any(|a| a == "--demo" || a == "--gui-demo");

    let initial_mode = if is_demo {
        probe::Mode::Demo {
            scenario: "supported".into(),
        }
    } else {
        probe::Mode::Real
    };

    let result = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|_app, _argv, _cwd| {}))
        .plugin(
            tauri_plugin_log::Builder::default()
                .level(log::LevelFilter::Info)
                .build(),
        )
        .setup(move |app| {
            let state = probe::start(app.handle(), initial_mode)?;
            app.manage(state);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            commands::probe_discover,
            commands::probe_plan,
            commands::probe_recovery_status,
            commands::probe_start_run,
            commands::probe_run_result,
            commands::probe_clear_recovery,
            commands::probe_set_mode,
            commands::probe_open_results,
        ])
        .run(tauri::generate_context!());

    if let Err(error) = result {
        log::error!("failed to start: {error}");
        eprintln!("Vetro Probe failed to start: {error}");
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use serde_json::Value;

    const CAPABILITY_JSON: &str = include_str!("../capabilities/default.json");
    const TAURI_CONF_JSON: &str = include_str!("../tauri.conf.json");

    #[test]
    fn capability_file_is_valid_and_grants_event_permissions_to_main_window() {
        let cap: Value = serde_json::from_str(CAPABILITY_JSON)
            .expect("probe-app capabilities/default.json must be valid JSON");

        // 1. Window matching
        let windows = cap.get("windows").and_then(|w| w.as_array())
            .expect("capabilities/default.json must define a 'windows' array");
        let window_names: Vec<&str> = windows.iter().filter_map(|w| w.as_str()).collect();
        assert!(
            window_names.contains(&"main"),
            "capabilities/default.json must cover window 'main', got: {:?}",
            window_names
        );

        // 2. Event permissions for streaming research progress
        let perms = cap.get("permissions").and_then(|p| p.as_array())
            .expect("capabilities/default.json must define a 'permissions' array");
        let perm_names: Vec<&str> = perms.iter().filter_map(|p| p.as_str()).collect();

        let has_listen = perm_names.contains(&"core:event:allow-listen")
            || perm_names.contains(&"core:event:default")
            || perm_names.contains(&"core:default");
        assert!(
            has_listen,
            "capabilities/default.json must grant event listen permission (got: {:?})",
            perm_names
        );

        let has_unlisten = perm_names.contains(&"core:event:allow-unlisten")
            || perm_names.contains(&"core:event:default")
            || perm_names.contains(&"core:default");
        assert!(
            has_unlisten,
            "capabilities/default.json must grant event unlisten permission (got: {:?})",
            perm_names
        );

        // 3. Least privilege: no broad filesystem, shell, process, or wildcard permissions
        for perm in &perm_names {
            assert!(
                !perm.contains('*'),
                "capabilities/default.json must not use wildcards: {perm}"
            );
            assert!(
                !perm.starts_with("shell:") && !perm.starts_with("fs:"),
                "capabilities/default.json must not grant dangerous shell/fs permissions: {perm}"
            );
        }
    }

    #[test]
    fn tauri_conf_windows_matches_capability_main_window() {
        let conf: Value = serde_json::from_str(TAURI_CONF_JSON)
            .expect("probe-app tauri.conf.json must be valid JSON");
        let windows = conf
            .pointer("/app/windows")
            .and_then(|w| w.as_array())
            .expect("tauri.conf.json must define app.windows");
        assert!(!windows.is_empty(), "app.windows must not be empty");

        let first_label = windows[0]
            .get("label")
            .and_then(|l| l.as_str())
            .unwrap_or("main");
        assert_eq!(
            first_label, "main",
            "first window label in tauri.conf.json must be 'main'"
        );
    }
}
