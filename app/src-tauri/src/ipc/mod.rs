//! The IPC contract between the UI and `pcore`.
//!
//! Three mechanisms, chosen per traffic shape (spec.md FR11,
//! architecture/INITIAL_REVIEW.md §6). This split is not an optimisation to
//! apply later: retrofitting an ordered stream onto code already wired through
//! the general event bus is a breaking change to the contract, not an addition.
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
//!
//! # The names are the contract, and they are checked
//!
//! Every command and event name is a constant here and a constant in
//! `app/src/ipc.ts`, and the two files are compared by a test in this module.
//! The reason is that a mismatch is silent in both directions -- an `invoke` for
//! a command that does not exist rejects into whatever the caller does with a
//! rejected promise, and an emit to a name nobody listens for succeeds. Neither
//! shows up as a build failure, and both show up as a screen that is simply
//! empty.

pub mod channels;
pub mod commands;
pub mod events;

use pcore::DeviceService;
use tauri::Manager;

/// The application's one handle on the device thread.
///
/// No lock, and that is the payoff for the worker existing: the handle is
/// `Sync` because holding it grants no access to a device, only the ability to
/// ask the thread that has one. A `Mutex<Peripheral>` here would have been the
/// shape spec.md FR10 rules out -- several callers taking turns with one HID
/// handle from whatever thread the framework happened to use.
pub struct Devices {
    service: DeviceService,
}

impl Devices {
    pub fn new(service: DeviceService) -> Self {
        Self { service }
    }

    pub fn service(&self) -> &DeviceService {
        &self.service
    }
}

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

#[cfg(test)]
mod tests {
    #![allow(clippy::expect_used, clippy::panic, clippy::unwrap_used)]

    /// The frontend's half of the contract, read at compile time.
    ///
    /// `include_str!` rather than a runtime file read, so this cannot pass by
    /// silently failing to find the file on a machine where the working
    /// directory is somewhere else.
    const FRONTEND: &str = include_str!("../../../src/ipc.ts");

    #[test]
    fn every_command_name_is_spelled_the_same_on_both_sides() {
        // The failure this catches: someone renames a command in the
        // `generate_handler!` list and the frontend keeps invoking the old
        // name. Nothing fails to compile; the screen is just empty, and the
        // reason is a rejected promise nobody logged.
        for name in super::commands::ALL {
            assert!(
                FRONTEND.contains(&format!("\"{name}\"")),
                "the frontend does not invoke `{name}`; app/src/ipc.ts and \
                 app/src-tauri/src/ipc/commands.rs have drifted"
            );
        }
    }

    #[test]
    fn every_event_name_is_spelled_the_same_on_both_sides() {
        // Worse than the command case, because emitting to a name nobody
        // listens for *succeeds*. A device could be unplugged and the screen
        // would keep showing it, with no error anywhere.
        for name in super::events::ALL {
            assert!(
                FRONTEND.contains(&format!("\"{name}\"")),
                "the frontend does not listen for `{name}`; app/src/ipc.ts and \
                 app/src-tauri/src/ipc/events.rs have drifted"
            );
        }
    }

    #[test]
    fn the_frontend_invokes_nothing_this_crate_does_not_handle() {
        // The other direction. A frontend calling a command that was never
        // registered fails at runtime on a user's machine, which is the worst
        // place to find out.
        for invoked in invoked_names(FRONTEND) {
            assert!(
                super::commands::ALL.contains(&invoked.as_str()),
                "app/src/ipc.ts invokes `{invoked}`, which no command in this \
                 crate handles"
            );
        }
    }

    #[test]
    fn the_analog_stream_is_not_wired_through_the_event_bus() {
        // The FR11 rule that is a blocking review finding if broken. A waveform
        // delivered through the general event bus can arrive out of order, and
        // an out-of-order waveform is wrong data rather than late data -- while
        // looking, on screen, exactly like a real measurement.
        assert!(
            FRONTEND.contains("new Channel"),
            "the analog stream must be subscribed through a Tauri channel"
        );
        for line in FRONTEND.lines() {
            let mentions_stream = line.contains("analog") || line.contains("Analog");
            assert!(
                !(mentions_stream && line.contains("listen<")),
                "an analog stream was subscribed through the event bus: {line}"
            );
        }
    }

    /// Every string literal passed to `invoke(...)` in the frontend.
    ///
    /// A small scanner rather than a parser, and that is deliberate: it only
    /// has to understand the one shape this project writes, which is
    /// `invoke<T>("name"` or `invoke("name"`. Anything else in that file is a
    /// call site the convention says should not exist.
    ///
    /// Comments are removed first, because that file explains the contract in
    /// prose and prose about `invoke` is not a call to it -- a scanner that
    /// could not tell the difference would make the documentation unwritable.
    fn invoked_names(source: &str) -> Vec<String> {
        let source = strip_comments(source);
        let mut names = Vec::new();
        let mut rest = source.as_str();
        while let Some(at) = rest.find("invoke") {
            rest = &rest[at + "invoke".len()..];
            let Some(open) = rest.find('(') else { break };
            let head = &rest[..open];
            // Only a generic argument may sit between `invoke` and its paren.
            if !head.trim().is_empty() && !head.trim_start().starts_with('<') {
                continue;
            }
            let after = &rest[open + 1..];
            let Some(quote) = after.find('"') else {
                continue;
            };
            if after[..quote].trim() != "" {
                continue;
            }
            let literal = &after[quote + 1..];
            let Some(end) = literal.find('"') else { break };
            names.push(literal[..end].to_owned());
        }
        names
    }

    /// Source with `//` and block comments removed. Strings are not tracked,
    /// which is safe here: no command name in this file contains a slash.
    fn strip_comments(source: &str) -> String {
        let mut out = String::with_capacity(source.len());
        let mut rest = source;
        loop {
            let line = rest.find("//");
            let block = rest.find("/*");
            match (line, block) {
                (None, None) => {
                    out.push_str(rest);
                    return out;
                }
                (Some(at), None) => {
                    out.push_str(&rest[..at]);
                    let end = rest[at..].find('\n').map_or(rest.len(), |n| at + n);
                    rest = &rest[end..];
                }
                (None, Some(at)) => {
                    out.push_str(&rest[..at]);
                    let end = rest[at..].find("*/").map_or(rest.len(), |n| at + n + 2);
                    rest = &rest[end..];
                }
                (Some(l), Some(b)) if l < b => {
                    out.push_str(&rest[..l]);
                    let end = rest[l..].find('\n').map_or(rest.len(), |n| l + n);
                    rest = &rest[end..];
                }
                (Some(_), Some(b)) => {
                    out.push_str(&rest[..b]);
                    let end = rest[b..].find("*/").map_or(rest.len(), |n| b + n + 2);
                    rest = &rest[end..];
                }
            }
        }
    }

    #[test]
    fn the_scanner_does_not_mistake_prose_for_a_call() {
        assert!(invoked_names("/** talks about invoke(\"nope\") */").is_empty());
        assert!(invoked_names("// invoke(\"nope\")").is_empty());
        assert_eq!(
            invoked_names("const x = invoke<string>(\"yes\");"),
            vec!["yes".to_owned()]
        );
    }

    #[test]
    fn the_scanner_finds_the_calls_it_is_supposed_to_find() {
        // A scanner nobody checks is a test that passes by finding nothing.
        let found = invoked_names(FRONTEND);
        assert!(
            found.contains(&"list_devices".to_owned()),
            "the scanner found {found:?}, which does not look like this file"
        );
        assert!(found.len() >= 4, "found only {found:?}");
    }
}
