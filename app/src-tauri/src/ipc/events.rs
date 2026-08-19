//! Mechanism 2: events. Rare notifications, always backend-initiated.
//!
//! For things the UI cannot know to ask about: a device appeared or went away,
//! its state changed under it, the protocol errored. Low frequency by
//! definition -- anything that arrives faster than a user can read it belongs in
//! a channel (see [`super::channels`]).
//!
//! Event names are declared here as constants rather than written as string
//! literals at each call site, because a typo in an event name is silent on both
//! sides: the emitter succeeds and no listener ever fires. `super::tests` checks
//! that each one appears in the frontend's declaration file too.
//!
//! # Why an update event exists as well as a connected one
//!
//! Because a device changing state is not the same news as a device arriving,
//! and one name for both would make the name wrong half the time. A card that
//! moved from "ready" to "connected", or to "stalled", is the same device with
//! different facts about it, and a UI that treated every such message as an
//! arrival would grow duplicates in its list.

use pcore::ServiceEvent;
use serde::Serialize;
use tauri::{AppHandle, Emitter, Runtime};

/// A device appeared and has been identified as far as enumeration allows.
/// Payload: a [`DeviceCard`].
pub const DEVICE_CONNECTED: &str = "device:connected";
/// A device already on the list changed state. Payload: a [`DeviceCard`].
pub const DEVICE_UPDATED: &str = "device:updated";
/// A device disappeared from the bus. Payload: [`DeviceGoneEvent`].
pub const DEVICE_DISCONNECTED: &str = "device:disconnected";
/// Charge or charging state changed on a wireless device.
///
/// Declared and not yet emitted: no device this project speaks to reports a
/// charge, and inventing a number to fill the field would be worse than the
/// field being quiet. TICKET-19/20 fill it.
pub const BATTERY_CHANGED: &str = "device:battery-changed";
/// The device's config endpoint failed. Payload: [`ProtocolErrorEvent`].
pub const PROTOCOL_ERROR: &str = "device:protocol-error";

pub const ALL: &[&str] = &[
    DEVICE_CONNECTED,
    DEVICE_UPDATED,
    DEVICE_DISCONNECTED,
    BATTERY_CHANGED,
    PROTOCOL_ERROR,
];

/// Payload for [`DEVICE_DISCONNECTED`].
///
/// The id and nothing else, deliberately. A card for a device that is no longer
/// there would invite a screen to keep rendering it.
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DeviceGoneEvent {
    pub device_id: u64,
}

/// Payload for [`PROTOCOL_ERROR`].
///
/// `user_message` is the whole point of this event existing: a stall is not
/// recoverable in software, so the only useful output is an instruction to the
/// person holding the keyboard. `recoverable` is carried explicitly so the UI
/// never has to guess whether to offer a retry -- for a stall there is nothing to
/// retry, and offering one keeps the endpoint pinned.
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

/// Sends one thing `pcore` noticed to the window.
///
/// The only place a [`ServiceEvent`] becomes an event name, so the mapping is
/// readable in one screenful. Emission failures are dropped: the one way this
/// fails is a window that is already gone, and there is nothing to tell.
pub fn forward<R: Runtime>(app: &AppHandle<R>, event: ServiceEvent) {
    match event {
        ServiceEvent::Attached(card) => emit(app, DEVICE_CONNECTED, *card),
        ServiceEvent::Updated(card) => emit(app, DEVICE_UPDATED, *card),
        ServiceEvent::Detached { id } => {
            emit(app, DEVICE_DISCONNECTED, DeviceGoneEvent { device_id: id })
        }
        ServiceEvent::ProtocolError {
            id,
            user_message,
            recoverable,
        } => emit(
            app,
            PROTOCOL_ERROR,
            ProtocolErrorEvent {
                device_id: id,
                user_message,
                recoverable,
            },
        ),
    }
}

fn emit<R: Runtime, T: Serialize + Clone>(app: &AppHandle<R>, name: &str, payload: T) {
    if let Err(error) = app.emit(name, payload) {
        log::debug!("no listener for {name}: {error}");
    }
}
