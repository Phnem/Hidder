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
            match probe::start(app.handle(), initial_mode) {
                Ok(state) => {
                    app.manage(state);
                }
                Err(error) => {
                    log::error!("the Probe engine sidecar did not start: {error}");
                }
            }
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
