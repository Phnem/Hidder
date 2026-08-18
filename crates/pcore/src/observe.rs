//! Building a matcher observation from what enumeration reported.
//!
//! One conversion, in one place. It exists here rather than in `ptransport`
//! because `pregistry` and `ptransport` are both at the bottom of the graph and
//! must not know about each other -- the bridge belongs to whoever uses both.
//!
//! The serial number is reduced to a boolean on the way through and its value is
//! never carried. Whether a serial exists is a fingerprint signal; what it says
//! identifies one physical unit and is of no use in deciding what a device is.

use pregistry::{CollectionObservation, DeviceObservation};
use ptransport::{Hid, HidCollection};

/// Turns the collections belonging to one device into what the matcher may know.
pub fn from_collections(hid: &Hid, collections: &[&HidCollection]) -> DeviceObservation {
    let first = collections.first();
    let vendor_id = first.map_or(0, |c| c.vendor_id);
    let product_id = first.map_or(0, |c| c.product_id);

    let mut interfaces: Vec<i32> = collections.iter().map(|c| c.interface_number).collect();
    interfaces.sort_unstable();
    interfaces.dedup();

    let observed = collections
        .iter()
        .map(|collection| {
            let report = hid.report_shape(collection);
            CollectionObservation {
                interface: u8::try_from(collection.interface_number.max(0)).unwrap_or(0),
                usage_page: collection.usage_page,
                usage: collection.usage,
                descriptor_fnv1a64: report.descriptor_fnv1a64,
                report_id: report.report_id(),
                input_bytes: report.input_bytes(),
                output_bytes: report.output_bytes(),
                feature_bytes: report.feature_bytes(),
            }
        })
        .collect();

    DeviceObservation::from_enumeration(
        vendor_id,
        product_id,
        first.and_then(|c| c.manufacturer.clone()),
        first.and_then(|c| c.product.clone()),
        first.map_or(0, |c| c.release_number),
        first.is_some_and(|c| c.serial_number.is_some()),
        u8::try_from(interfaces.len()).unwrap_or(0),
        observed,
    )
}
