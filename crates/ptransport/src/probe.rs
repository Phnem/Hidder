//! A single-exchange channel, for the first conversation with a new family.
//!
//! # Why this is not `DeviceSession`
//!
//! `DeviceSession` (spec.md FR10) owns a handle on a dedicated worker thread and
//! serves an async queue, because the product needs concurrent reads, writes and
//! a continuous analog stream through one handle. None of that exists yet, and
//! none of it is needed to send one frame and wait for one answer.
//!
//! So this is deliberately the smaller thing: open, write once, read until a
//! deadline, close. It is blocking, it holds its handle for its own lifetime,
//! and it has no queue. What it must never become is the general I/O path --
//! when `DeviceSession` arrives, this stays what it is, or disappears.
//!
//! # What it will not do
//!
//! - It will not retry. [`ProbeChannel::write_report`] writes once and returns.
//! - It will not decide what an answer is. It hands back every report it saw,
//!   with the time each arrived, and the layer that owns the family's codec
//!   decides which if any of them answers the request.
//! - It will not strip or add a report id. The bytes in and the bytes out are
//!   what the backend saw, so a transcript of an exchange is a transcript of the
//!   wire and not of our idea of it.
//!
//! # What it does not enforce, said plainly
//!
//! [`ProbeChannel::write_report`] takes bytes. It has to: this crate is the
//! bottom of the dependency graph and must never depend on `psafety`, so it
//! cannot demand a type only `psafety` can mint. The guarantee that no
//! unauthorised bytes reach a device is therefore held one layer up -- the
//! product path goes through `psafety::ProbeGate` or `psafety::SafetyGate`, and
//! a caller that opens a channel and writes to it directly has stepped outside
//! that path.
//!
//! This is the same gap `psafety`'s crate documentation records for the write
//! path, and it is recorded here too rather than implied, because "the transport
//! only accepts authorised commands" is a thing someone could otherwise believe
//! from reading the layer above. It does not. Review is what keeps this module's
//! callers to the two gates.

use std::ffi::CString;
use std::time::{Duration, Instant};

use hidapi::HidDevice;

use crate::inventory::{Hid, HidCollection};
use crate::{DeviceId, TransportError};

/// Largest report this channel will read, comfortably over the 64 bytes the
/// families in scope use.
const MAX_REPORT: usize = 1024;

/// One report as it arrived, with the time since the write that preceded it.
#[derive(Clone, PartialEq, Eq, Debug)]
pub struct ReceivedReport {
    /// Time from the end of the write to this report arriving.
    pub after: Duration,
    /// Exactly what the backend produced, report id byte included when the
    /// device uses numbered reports.
    pub bytes: Vec<u8>,
}

/// An opened vendor collection, good for one exchange.
pub struct ProbeChannel {
    device: HidDevice,
    device_id: DeviceId,
    report_id: u8,
    /// When the last write finished, so read timings are relative to it.
    wrote_at: Option<Instant>,
}

impl ProbeChannel {
    /// Opens a collection and derives its report id from its own descriptor.
    ///
    /// The report id is derived rather than supplied, which is both more robust
    /// and what the vendor's own driver does: it looks for the collection that
    /// has input reports *and* output reports and takes the first output
    /// report's id. Hardcoding the number our board happens to use would make
    /// the same code wrong on the next board in the family for no benefit.
    pub fn open(
        hid: &Hid,
        collection: &HidCollection,
        device_id: DeviceId,
    ) -> Result<Self, TransportError> {
        let path = CString::new(collection.path.as_str())
            .map_err(|_| TransportError::Backend("device path contains an interior NUL".into()))?;
        let device = hid
            .open_path(&path)
            .map_err(TransportError::Backend)?;

        let mut buffer = vec![0u8; 4096];
        let len = device
            .get_report_descriptor(&mut buffer)
            .map_err(|error| TransportError::Backend(error.to_string()))?;
        buffer.truncate(len);

        let report_id = bidirectional_report_id(&buffer).ok_or_else(|| {
            TransportError::Backend(
                "no report id on this collection carries both input and output reports".into(),
            )
        })?;

        Ok(Self {
            device,
            device_id,
            report_id,
            wrote_at: None,
        })
    }

    pub fn device(&self) -> DeviceId {
        self.device_id
    }

    /// The report id derived from the descriptor.
    pub fn report_id(&self) -> u8 {
        self.report_id
    }

    /// Writes one output report. Once.
    ///
    /// Returns the exact bytes handed to the backend -- the report id followed
    /// by the frame -- so the caller can record what went out without
    /// reconstructing it.
    pub fn write_report(&mut self, frame: &[u8]) -> Result<Vec<u8>, TransportError> {
        let mut buffer = Vec::with_capacity(frame.len() + 1);
        buffer.push(self.report_id);
        buffer.extend_from_slice(frame);

        self.device
            .write(&buffer)
            .map_err(|error| TransportError::Backend(error.to_string()))?;
        self.wrote_at = Some(Instant::now());
        Ok(buffer)
    }

    /// Reads one report, or `None` if the deadline passed first.
    ///
    /// A timeout is not an error here. Nothing was heard, which is a result the
    /// caller has to be able to record and stop on, rather than an exception to
    /// recover from by sending something else.
    pub fn read_report(&self, timeout: Duration) -> Result<Option<ReceivedReport>, TransportError> {
        let millis = i32::try_from(timeout.as_millis()).unwrap_or(i32::MAX);
        let mut buffer = vec![0u8; MAX_REPORT];
        let read = self
            .device
            .read_timeout(&mut buffer, millis)
            .map_err(|error| TransportError::Backend(error.to_string()))?;
        if read == 0 {
            return Ok(None);
        }
        buffer.truncate(read);
        Ok(Some(ReceivedReport {
            after: self.wrote_at.map(|at| at.elapsed()).unwrap_or_default(),
            bytes: buffer,
        }))
    }
}

/// The first report id on this descriptor that carries both an input and an
/// output item.
///
/// A minimal HID item walk: enough to answer this one question, and no more. It
/// understands short items and skips long ones, tracks the current Report ID
/// global, and records which of them are seen by an Input or an Output main
/// item.
///
/// Deliberately not a general report-descriptor parser. That is a real piece of
/// work with real edge cases, and a half-written one that silently guesses is
/// worse than none: here, a descriptor this cannot make sense of yields `None`
/// and the caller refuses to open, which is the correct outcome.
fn bidirectional_report_id(descriptor: &[u8]) -> Option<u8> {
    const MAIN_INPUT: u8 = 0x80;
    const MAIN_OUTPUT: u8 = 0x90;
    const GLOBAL_REPORT_ID: u8 = 0x84;
    const LONG_ITEM: u8 = 0xFE;

    // Index is the report id; the pair is (seen by an input item, seen by an
    // output item). Report id 0 means "unnumbered", which cannot be what we are
    // looking for: an unnumbered collection has no id to send.
    let mut seen = [(false, false); 256];
    let mut current: u8 = 0;
    let mut order: Vec<u8> = Vec::new();

    let mut i = 0usize;
    while i < descriptor.len() {
        let prefix = descriptor[i];
        if prefix == LONG_ITEM {
            // Long item: [0xFE, data size, tag, data...]
            let size = *descriptor.get(i + 1)? as usize;
            i += 3 + size;
            continue;
        }
        let size = match prefix & 0x03 {
            0 => 0usize,
            1 => 1,
            2 => 2,
            _ => 4,
        };
        let data = descriptor.get(i + 1..i + 1 + size)?;
        let tag = prefix & 0xFC;

        match tag {
            GLOBAL_REPORT_ID => {
                current = *data.first()?;
                if current != 0 && !order.contains(&current) {
                    order.push(current);
                }
            }
            MAIN_INPUT if current != 0 => seen[usize::from(current)].0 = true,
            MAIN_OUTPUT if current != 0 => seen[usize::from(current)].1 = true,
            _ => {}
        }
        i += 1 + size;
    }

    order
        .into_iter()
        .find(|id| seen[usize::from(*id)] == (true, true))
}

impl Hid {
    /// Opens a device path. Crate-internal so `hidapi` stays out of the public
    /// API; [`ProbeChannel::open`] is the only caller.
    pub(crate) fn open_path(&self, path: &CString) -> Result<HidDevice, String> {
        self.api_ref()
            .open_path(path)
            .map_err(|error| error.to_string())
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::expect_used, clippy::panic, clippy::unwrap_used)]

    use super::*;

    /// Our own board's `0xFF60:0x0061` descriptor, captured in TICKET-08.
    const AULA_VENDOR_COLLECTION: [u8; 36] = [
        0x06, 0x60, 0xFF, // Usage Page (Vendor 0xFF60)
        0x09, 0x61, // Usage (0x61)
        0xA1, 0x01, // Collection (Application)
        0x85, 0x09, //   Report ID (9)
        0x09, 0x62, //   Usage (0x62)
        0x15, 0x00, //   Logical Minimum (0)
        0x26, 0xFF, 0x00, //   Logical Maximum (255)
        0x75, 0x08, //   Report Size (8)
        0x95, 0x3F, //   Report Count (63)
        0x81, 0x02, //   Input (Data, Var, Abs)
        0x09, 0x63, //   Usage (0x63)
        0x15, 0x00, //   Logical Minimum (0)
        0x26, 0xFF, 0x00, //   Logical Maximum (255)
        0x75, 0x08, //   Report Size (8)
        0x95, 0x3F, //   Report Count (63)
        0x91, 0x02, //   Output (Data, Var, Abs)
        0xC0, // End Collection
    ];

    #[test]
    fn our_own_board_yields_report_id_nine() {
        // The number is not the point: the derivation is. This asserts that the
        // rule the vendor uses, applied to the descriptor we captured
        // ourselves, produces the id TICKET-08 recorded.
        assert_eq!(bidirectional_report_id(&AULA_VENDOR_COLLECTION), Some(9));
    }

    #[test]
    fn an_input_only_collection_yields_nothing() {
        // The first vendor collection on the same board, 0xFF00:0x0001, carries
        // feature reports only. Nothing may be written to it, and the way that
        // is enforced is that no report id comes back.
        let mut descriptor = AULA_VENDOR_COLLECTION.to_vec();
        // Turn the Output main item into a Feature one (tag 0xB0).
        let output = descriptor.len() - 3;
        assert_eq!(descriptor[output], 0x91);
        descriptor[output] = 0xB1;
        assert_eq!(bidirectional_report_id(&descriptor), None);
    }

    #[test]
    fn an_unnumbered_collection_yields_nothing() {
        let descriptor: Vec<u8> = AULA_VENDOR_COLLECTION
            .iter()
            .copied()
            .enumerate()
            .filter(|(i, _)| *i != 7 && *i != 8)
            .map(|(_, b)| b)
            .collect();
        assert_eq!(
            bidirectional_report_id(&descriptor),
            None,
            "a collection with no report id has no id to send"
        );
    }

    #[test]
    fn a_truncated_descriptor_yields_nothing_rather_than_a_guess() {
        for cut in 1..AULA_VENDOR_COLLECTION.len() {
            let partial = &AULA_VENDOR_COLLECTION[..cut];
            let derived = bidirectional_report_id(partial);
            assert!(
                derived.is_none() || derived == Some(9),
                "a truncated descriptor produced {derived:?}"
            );
        }
    }

    #[test]
    fn the_first_bidirectional_id_wins_when_there_are_several() {
        let mut descriptor = Vec::new();
        descriptor.extend_from_slice(&[0x06, 0x60, 0xFF, 0x09, 0x61, 0xA1, 0x01]);
        // Report id 3: input only.
        descriptor.extend_from_slice(&[0x85, 0x03, 0x81, 0x02]);
        // Report id 7: input and output.
        descriptor.extend_from_slice(&[0x85, 0x07, 0x81, 0x02, 0x91, 0x02]);
        // Report id 9: also both, but later.
        descriptor.extend_from_slice(&[0x85, 0x09, 0x81, 0x02, 0x91, 0x02]);
        descriptor.push(0xC0);
        assert_eq!(bidirectional_report_id(&descriptor), Some(7));
    }
}
