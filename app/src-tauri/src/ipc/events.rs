//! Mechanism 2: events. Rare notifications, always backend-initiated.
//!
//! For things the UI cannot know to ask about: a device appeared or went away,
//! a charge level changed, the protocol errored. Low frequency by definition --
//! anything that arrives faster than a user can read it belongs in a channel
//! (see [`super::channels`]).
//!
//! Event names are declared here as constants rather than written as string
//! literals at each call site, because a typo in an event name is silent on both
//! sides: the emitter succeeds and no listener ever fires.

use serde::Serialize;

/// A device was connected or identified.
pub const DEVICE_CONNECTED: &str = "device:connected";
/// A device disappeared from the bus.
pub const DEVICE_DISCONNECTED: &str = "device:disconnected";
/// Charge or charging state changed on a wireless device.
pub const BATTERY_CHANGED: &str = "device:battery-changed";
/// The device's config endpoint stalled. Terminal until it is reconnected.
pub const PROTOCOL_ERROR: &str = "device:protocol-error";

/// Payload for [`PROTOCOL_ERROR`].
///
/// `user_message` is the whole point of this event existing: a stall is not
/// recoverable in software, so the only useful output is an instruction to the
/// person holding the keyboard. `recoverable` is carried explicitly so the UI
/// never has to guess whether to offer a retry button -- for a stall there is
/// nothing to retry, and offering one keeps the endpoint pinned.
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ProtocolErrorEvent {
    pub device_id: u64,
    pub user_message: String,
    pub recoverable: bool,
}

/// Payload for [`BATTERY_CHANGED`].
///
/// Deliberately says nothing about where the value came from. Charge has three
/// possible sources depending on the device (a standard HID usage, a vendor
/// opcode, or a query to a wireless receiver rather than the device itself) and
/// the capability layer's job is to make that difference invisible up here.
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BatteryEvent {
    pub device_id: u64,
    pub percent: u8,
    pub charging: bool,
}
