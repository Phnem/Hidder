//! HID transport.
//!
//! Skeleton only: TICKET-07 fixes this crate's public contract and dependency
//! direction. Behaviour arrives with TICKET-08 (Windows HID inventory) and
//! TICKET-11.
//!
//! # What this crate hides
//!
//! Every OS HID API and every physical device handle. `hidapi` is the primary
//! abstraction (spec.md FR7); direct platform calls (`HidD_*`, native
//! `IOHIDManager`, `hidraw` ioctls) are an escape hatch that lives in
//! [`platform`] and never leaves this crate. No `#[cfg(windows)]` above here.
//!
//! # The ownership rule
//!
//! A device is reached only as a [`DeviceId`] plus a [`SessionHandle`]. The
//! physical handle is owned by exactly one session, which performs all
//! blocking HID I/O on its own dedicated worker thread; callers talk to it
//! through an async command queue (spec.md FR10).
//!
//! This is stronger than hiding the OS type, and the reason is concrete:
//! `hidapi::HidDevice` is `Send` but not `Sync`, and HID reads block. Without
//! this boundary the layers above converge on a mutex around one handle with
//! concurrent read, write and stream traffic through it. Prior art shows that
//! shape working right up until a continuous analog stream exists, at which
//! point the reader holds the lock (see docs/prior-art/sharkfin-methods.md).

pub mod inventory;
pub mod platform;
pub mod probe;

pub use inventory::{CollectionAccess, Hid, HidCollection};
pub use probe::{ProbeChannel, ReceivedReport, ReportChannel};

/// Stable identity of a device for the lifetime of a session.
///
/// Deliberately not the OS device path: the path is a transport detail, it
/// differs per platform, and on Windows one physical keyboard exposes several.
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Debug)]
pub struct DeviceId(u64);

impl DeviceId {
    pub fn new(raw: u64) -> Self {
        Self(raw)
    }

    pub fn raw(self) -> u64 {
        self.0
    }
}

/// The only way to reach an open device.
///
/// Cloneable and cheap on purpose: several callers may hold a handle to the
/// same session, because holding a handle grants no access to the underlying
/// device handle. Every operation goes through the session's queue.
#[derive(Clone, Debug)]
pub struct SessionHandle {
    device: DeviceId,
}

impl SessionHandle {
    pub fn device(&self) -> DeviceId {
        self.device
    }
}

/// Transport failures.
///
/// `EndpointStalled` is a distinct variant by design. It is not "an I/O error
/// that happened during a write": the firmware's control endpoint is wedged,
/// reopening the device does not clear it, and further traffic keeps it pinned,
/// so the whole device must go quiet until it is physically reconnected
/// (spec.md § Failure and fallback behavior).
///
/// # Why no `#[from] hidapi::HidError`
///
/// It was there in the skeleton and is gone as of TICKET-08. An error type is
/// not a device handle, so it leaked nothing dangerous, but it did put a backend
/// type in this crate's public API, and every caller that matched on it would
/// have been matching on `hidapi`'s shape.
///
/// `Backend` carries the backend's message as a string on purpose, and callers
/// must treat it as opaque text for a log or a bug report. Do not match on its
/// contents: see the finding recorded in TICKET-08 -- on Windows this text is
/// the operating system's localised error message, so any substring test passes
/// or fails depending on the user's display language.
#[derive(Debug, thiserror::Error)]
pub enum TransportError {
    #[error("device {0:?} is not connected")]
    NotConnected(DeviceId),
    #[error("the device's config endpoint has stalled; it must be reconnected")]
    EndpointStalled,
    #[error("the device node could not be opened (on Linux this is usually a missing udev rule)")]
    AccessDenied,
    #[error("hid backend: {0}")]
    Backend(String),
}

impl TransportError {
    pub fn is_stall(&self) -> bool {
        matches!(self, TransportError::EndpointStalled)
    }
}
