//! Desktop application shell.
//!
//! Skeleton only (TICKET-07). Screens arrive with TICKET-13, real device state
//! with TICKET-12.
//!
//! This layer owns presentation and the IPC contract, and nothing else. Device
//! knowledge lives in `pcore` and below.

pub mod ipc;

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
        // All three IPC mechanisms are wired from the skeleton. The channel
        // command refuses for now rather than being absent, so the contract is
        // real and the frontend can be written against it.
        .invoke_handler(tauri::generate_handler![
            ipc::commands::build_id,
            ipc::commands::list_devices,
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
}
