//! Mechanism 3: channels. Ordered, high-throughput streams.
//!
//! Mandatory for the analog travel stream and for Learning Mode capture streams.
//! Not an alternative to events for these: the general event bus does not
//! guarantee ordering for async listeners, and an out-of-order analog waveform is
//! wrong data rather than late data.
//!
//! Real streaming arrives with TICKET-15. The contract is fixed here so that
//! ticket writes against it from the first line.

use serde::Serialize;
use tauri::ipc::Channel;

/// The command that hands a channel down. Declared like the others so the
/// contract test can see it, even though it does nothing yet.
pub const SUBSCRIBE_ANALOG_STREAM: &str = "subscribe_analog_stream";

/// One sample of key travel.
///
/// `value` is normalised travel in 0..=1, never raw sensor units. Devices report
/// on their own scales -- one known board reports roughly 0 to 350 -- and the
/// conversion belongs in the engine with its scale factor in the registry's
/// quirks, not in the UI and not hardcoded anywhere.
///
/// `seq` exists so the receiver can *detect* a gap. Ordering is the channel's
/// job; proving nothing was silently dropped is not, and for a waveform used as
/// write verification a dropped sample must be visible rather than smoothed over.
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AnalogSample {
    pub seq: u64,
    pub key: u16,
    pub value: f32,
}

/// Subscribe to the analog travel stream for a device.
///
/// Returns an error until TICKET-15. Note for that ticket: enabling the stream is
/// a *write* on at least one known family (a feature report turns analog mode on),
/// so this path goes through `psafety` like any other write. It is not exempt for
/// being a read-only feature to the user (docs/prior-art/royuan.md).
#[tauri::command]
pub fn subscribe_analog_stream(
    device_id: u64,
    channel: Channel<AnalogSample>,
) -> Result<(), String> {
    let _ = (device_id, channel);
    Err("analog stream is not implemented yet (TICKET-15)".into())
}
