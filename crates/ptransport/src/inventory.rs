//! Read-only HID enumeration.
//!
//! Introduced by TICKET-08. This module answers "what HID collections exist on
//! this machine, and what can we learn about them without touching the device".
//!
//! # Read-only means read-only
//!
//! Nothing here writes to a device, and that is a correctness property, not a
//! convention:
//!
//! - no `write`, no `send_feature_report`, no output reports;
//! - no `get_feature_report` either. That one reads, but it *sends a request* to
//!   the firmware to do so, which makes it a probe. Probing belongs behind the
//!   safety gate and the learning-mode allowlist, not in enumeration;
//! - no input reports are read. Beyond being unnecessary here, an input report
//!   from a keyboard collection is a keystroke, and this crate must never be the
//!   place that starts collecting those (spec.md FR8).
//!
//! What is left is what the operating system already knows: the enumeration
//! entries, the string descriptors it cached, and the report descriptor. On
//! Windows the report descriptor is reconstructed by the backend from the OS's
//! preparsed data rather than fetched from the device, so even that involves no
//! traffic on the wire.
//!
//! # Generic on purpose
//!
//! There is no device-specific path in here and there must never be one. The
//! same code inventories every device (TICKET-08 for one board, TICKET-09 for
//! the next); if a second device needs a different path, that is a finding about
//! this module, to be fixed here.

use hidapi::HidApi;

use crate::TransportError;

/// Largest report descriptor the HID specification allows.
const MAX_REPORT_DESCRIPTOR: usize = 4096;

/// The HID backend, owned so that no `hidapi` type appears in a public
/// signature.
///
/// Enumeration is a separate concern from a device session: this holds no
/// device handle and performs no I/O. `DeviceSession` (spec.md FR10) is what
/// owns a handle, and it arrives with the first real protocol work.
pub struct Hid {
    api: HidApi,
}

/// One HID top-level collection as the operating system reports it.
///
/// "Collection", not "device", is the right unit here. One physical keyboard
/// commonly presents several: a boot keyboard collection, one or more consumer
/// control collections, and often a vendor-defined one. They have separate
/// paths and separate access rules, so treating the physical product as one
/// entry would hide exactly the thing this ticket is trying to observe.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct HidCollection {
    /// OS-specific device node. Meaningful only to the backend, and it changes
    /// between reboots on Windows, so never persist it as an identity.
    pub path: String,
    pub vendor_id: u16,
    pub product_id: u16,
    pub usage_page: u16,
    pub usage: u16,
    pub interface_number: i32,
    pub release_number: u16,
    pub manufacturer: Option<String>,
    pub product: Option<String>,
    pub serial_number: Option<String>,
}

impl HidCollection {
    /// Whether the usage page falls in the vendor-defined range.
    ///
    /// A search heuristic, not a claim about the device: a vendor-defined
    /// collection is where a configuration channel usually lives, but its
    /// presence proves nothing about which protocol family the device speaks,
    /// and its absence proves nothing either.
    pub fn is_vendor_defined(&self) -> bool {
        (0xFF00..=0xFFFF).contains(&self.usage_page)
    }
}

/// What was learned by trying to open a collection.
#[derive(Clone, Debug, Default)]
pub struct CollectionAccess {
    /// A handle was obtained.
    ///
    /// This is weaker than "we can talk to it". On Windows the backend falls
    /// back to opening with no access rights when read/write is refused, which
    /// still yields descriptors and strings but no I/O. Distinguishing the two
    /// requires sending a report, which this ticket forbids, so a `true` here
    /// means only that enumeration-level access succeeded.
    pub opened: bool,
    /// The backend's message when opening failed. Opaque text for a report; do
    /// not match on it (it is localised on Windows).
    pub open_error: Option<String>,
    /// Raw report descriptor, when the backend could produce one.
    pub report_descriptor: Option<Vec<u8>>,
    pub descriptor_error: Option<String>,
}

impl Hid {
    pub fn open() -> Result<Self, TransportError> {
        HidApi::new()
            .map(|api| Self { api })
            .map_err(|e| TransportError::Backend(e.to_string()))
    }

    /// Every HID top-level collection the OS is willing to enumerate.
    ///
    /// No filtering by vendor id. Filtering belongs to the caller: a registry
    /// that only scans for vendor ids it already knows can never discover
    /// anything new, and this is the layer that would make that mistake
    /// permanent.
    pub fn enumerate(&self) -> Vec<HidCollection> {
        let mut collections: Vec<HidCollection> = self
            .api
            .device_list()
            .map(|device| HidCollection {
                path: device.path().to_string_lossy().into_owned(),
                vendor_id: device.vendor_id(),
                product_id: device.product_id(),
                usage_page: device.usage_page(),
                usage: device.usage(),
                interface_number: device.interface_number(),
                release_number: device.release_number(),
                manufacturer: device.manufacturer_string().map(str::to_owned),
                product: device.product_string().map(str::to_owned),
                serial_number: device.serial_number().map(str::to_owned),
            })
            .collect();

        // Stable order, so two runs (and two devices) produce diffable reports.
        // Enumeration order is whatever the OS felt like today.
        collections.sort_by(|a, b| {
            (
                a.vendor_id,
                a.product_id,
                a.interface_number,
                a.usage_page,
                a.usage,
                &a.path,
            )
                .cmp(&(
                    b.vendor_id,
                    b.product_id,
                    b.interface_number,
                    b.usage_page,
                    b.usage,
                    &b.path,
                ))
        });
        collections
    }

    /// Open a collection read-only and read what the OS knows about it.
    ///
    /// The handle is dropped before returning. Failures are recorded rather than
    /// returned: on a machine full of HID devices, some collections are always
    /// unopenable, and that is the observation being collected, not an error
    /// that should abort the run.
    pub fn inspect(&self, collection: &HidCollection) -> CollectionAccess {
        let path = match std::ffi::CString::new(collection.path.as_str()) {
            Ok(path) => path,
            Err(_) => {
                return CollectionAccess {
                    open_error: Some("device path contains an interior NUL".into()),
                    ..Default::default()
                };
            }
        };

        let device = match self.api.open_path(&path) {
            Ok(device) => device,
            Err(error) => {
                return CollectionAccess {
                    open_error: Some(error.to_string()),
                    ..Default::default()
                };
            }
        };

        let mut buffer = vec![0u8; MAX_REPORT_DESCRIPTOR];
        let (report_descriptor, descriptor_error) = match device.get_report_descriptor(&mut buffer)
        {
            Ok(len) => {
                buffer.truncate(len);
                (Some(buffer), None)
            }
            Err(error) => (None, Some(error.to_string())),
        };

        CollectionAccess {
            opened: true,
            open_error: None,
            report_descriptor,
            descriptor_error,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn vendor_defined_range_is_the_hid_reserved_one() {
        let mut collection = HidCollection {
            path: String::new(),
            vendor_id: 0,
            product_id: 0,
            usage_page: 0x0001,
            usage: 0x0006,
            interface_number: 0,
            release_number: 0,
            manufacturer: None,
            product: None,
            serial_number: None,
        };
        assert!(!collection.is_vendor_defined(), "generic desktop page");

        collection.usage_page = 0x000C;
        assert!(!collection.is_vendor_defined(), "consumer page");

        collection.usage_page = 0xFF00;
        assert!(collection.is_vendor_defined(), "first vendor page");

        collection.usage_page = 0xFFFF;
        assert!(collection.is_vendor_defined(), "last vendor page");

        // Off by one at the bottom of the range: 0xFEFF is not vendor-defined,
        // and a heuristic that claimed it was would report a vendor channel on
        // devices that have none.
        collection.usage_page = 0xFEFF;
        assert!(!collection.is_vendor_defined());
    }
}
