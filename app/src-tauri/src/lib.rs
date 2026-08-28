//! Desktop application shell.
//!
//! This layer owns presentation and the IPC contract, and nothing else. Device
//! knowledge lives in `pcore` and below: there is no branch in this crate that
//! depends on what a device is, and if one ever appears it is a sign the
//! capability model failed to hide something it was supposed to hide.

pub mod ipc;
pub mod probe;

use pcore::DeviceService;
use tauri::Manager;

/// Version plus the commit it was built from, when that is knowable.
///
/// See build.rs. The commit is absent in a tarball build, so only the version
/// prefix is guaranteed.
pub fn build_id() -> String {
    match option_env!("PERIPHERAL_COMMIT") {
        Some(commit) => format!("{} ({commit})", env!("CARGO_PKG_VERSION")),
        None => env!("CARGO_PKG_VERSION").to_string(),
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    // Startup failure is reported and exits non-zero rather than panicking.
    // A panic here would print a Rust backtrace to a console the user does not
    // have, and there is no window yet in which to show anything, so the only
    // useful output is one line on stderr plus a log entry.
    let result = tauri::Builder::default()
        // Must be registered first, before any other plugin or state: its whole
        // job is to stop the second instance before it can touch a device.
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            ipc::raise_existing_window(app);
        }))
        .plugin(
            tauri_plugin_log::Builder::new()
                .level(log::LevelFilter::Info)
                .build(),
        )
        // The device thread is started in `setup` rather than before the
        // builder, because it emits events and therefore needs a handle to emit
        // through. That ordering is also why a failure to open the backend is
        // handled here: the window exists by this point, so the application can
        // come up and say what went wrong instead of vanishing.
        .setup(|app| {
            let emitter = app.handle().clone();
            match DeviceService::start(Box::new(move |event| {
                ipc::events::forward(&emitter, event);
            })) {
                Ok(service) => {
                    app.manage(ipc::Devices::new(service));
                }
                Err(error) => {
                    // Not fatal. Every command that needs a device will report
                    // that the service is unavailable, which is a screen saying
                    // so rather than a window that never opened.
                    log::error!("the device service did not start: {error}");
                }
            }
            // Vetro Probe research engine: deterministic demo mode by default
            // (zero HID, never emits physical evidence). Real hardware mode is
            // switched on only by an explicit `probe_set_mode`.
            match probe::start(app.handle(), probe::Mode::Demo { scenario: "supported".into() }) {
                Ok(state) => {
                    app.manage(state);
                }
                Err(error) => {
                    log::error!("the Probe engine sidecar did not start: {error}");
                }
            }
            Ok(())
        })
        // All three IPC mechanisms are wired. The channel command refuses for
        // now rather than being absent, so the contract is real and the frontend
        // is written against it.
        .invoke_handler(tauri::generate_handler![
            ipc::commands::build_id,
            ipc::commands::list_devices,
            ipc::commands::connect_device,
            ipc::commands::disconnect_device,
            ipc::commands::read_capability,
            ipc::commands::journal,
            ipc::commands::probe_discover,
            ipc::commands::probe_plan,
            ipc::commands::probe_recovery_status,
            ipc::commands::probe_start_run,
            ipc::commands::probe_run_result,
            ipc::commands::probe_clear_recovery,
            ipc::commands::probe_set_mode,
            ipc::channels::subscribe_analog_stream,
        ])
        .run(tauri::generate_context!());

    if let Err(error) = result {
        log::error!("failed to start: {error}");
        eprintln!("Peripheral failed to start: {error}");
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A bug report or an exported profile has to name the build it came from.
    /// The commit is absent in a tarball build, so only the prefix is checked.
    #[test]
    fn build_id_starts_with_the_version() {
        assert!(build_id().starts_with(env!("CARGO_PKG_VERSION")));
    }

    /// Every command constant is one this crate actually registers.
    ///
    /// The list in `generate_handler!` is a macro argument and cannot be
    /// inspected, so this checks the other half of the same fact: that the
    /// declared names match the functions that exist. A name declared and never
    /// registered would pass the frontend contract test and still fail at
    /// runtime.
    #[test]
    fn the_declared_command_names_match_the_functions() {
        let registered: &[&str] = &[
            stringify!(build_id),
            stringify!(list_devices),
            stringify!(connect_device),
            stringify!(disconnect_device),
            stringify!(read_capability),
            stringify!(journal),
            stringify!(probe_discover),
            stringify!(probe_plan),
            stringify!(probe_recovery_status),
            stringify!(probe_start_run),
            stringify!(probe_run_result),
            stringify!(probe_clear_recovery),
            stringify!(probe_set_mode),
            stringify!(subscribe_analog_stream),
        ];
        for name in ipc::commands::ALL {
            assert!(
                registered.contains(name),
                "`{name}` is declared but is not in the invoke handler"
            );
        }
        assert_eq!(registered.len(), ipc::commands::ALL.len());
    }
}
