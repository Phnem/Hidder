//! What was attempted, what happened, and how confidently we know it.
//!
//! Every attempt to reach a device produces one entry, including the ones that
//! were refused and including reads. A journal that records only successful
//! writes cannot answer the question it exists for -- "what did this program do
//! to my keyboard before it stopped working".
//!
//! # What an entry never contains
//!
//! Payload bytes. Not truncated, not hashed, not "only for writes": the length
//! and nothing else. Configuration payloads carry key layouts, key layouts carry
//! macros, and macros carry whatever someone recorded into them, which in
//! practice includes passwords. This journal is built to be attached to a bug
//! report, so it has to be safe to attach without anyone reviewing it first.
//!
//! Keystrokes never appear either, for a structural reason rather than a policy
//! one: this crate is on the command path and never sees an input report at all.
//! The capture-side privacy filter is a separate seam (spec.md FR8).

use pcaps::FamilyConfidence;
use ptransport::{DeviceId, TransportError};

use crate::class::OpcodeClass;

/// What the caller was trying to do, in the vocabulary a person understands.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Intent {
    Read,
    Probe,
    Write,
}

impl From<OpcodeClass> for Intent {
    fn from(class: OpcodeClass) -> Self {
        match class {
            OpcodeClass::SafeRead => Intent::Read,
            OpcodeClass::ProbeOk | OpcodeClass::BootstrapProbe => Intent::Probe,
            OpcodeClass::SafeWrite | OpcodeClass::SlowFlash => Intent::Write,
        }
    }
}

/// How a write was confirmed, once anything is able to confirm one.
///
/// The device's own answer is deliberately not a variant. An unsupported command
/// often replays the previous reply, so "the device said OK" is not evidence
/// (spec.md § Domain rules). Filling these in is TICKET-15's work; the field
/// exists now so that entries written before then are not silently missing it.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Verification {
    /// A read changes nothing, so there is nothing to verify.
    NotApplicable,
    /// A write happened and has not been confirmed yet.
    Pending,
    Confirmed {
        method: VerificationMethod,
    },
    Contradicted,
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum VerificationMethod {
    /// Read the value back and compared it.
    Readback,
    /// Watched the analog stream change.
    AnalogStream,
    /// A person looked at the keyboard and said so.
    UserObserved,
}

/// Why an attempt never reached the device.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Refusal {
    /// Nothing has told the gate what family this device speaks, so no command
    /// can be known to mean what it says.
    DeviceNotIdentified,
    /// The command belongs to a different family than the device. This is the
    /// collision case: the same byte is Debounce in one family and Options in
    /// another, and factory reset swaps numbers between them.
    WrongFamily {
        device_family: &'static str,
        command_family: &'static str,
    },
    /// The device is quarantined after a stall and stays that way until it is
    /// physically reconnected.
    Quarantined,
    /// The family this device speaks is not established well enough to write to
    /// it. Reads are unaffected: an unidentified device opens read-only rather
    /// than not at all (spec.md § Failure and fallback behavior).
    ///
    /// Note which confidence this is. Being certain which *product* is plugged
    /// in earns nothing here -- two products can be indistinguishable in
    /// structure and identical in every capability except the one that matters,
    /// and it is the opcode vocabulary that bricks boards.
    UnverifiedFamily { confidence: FamilyConfidence },
    /// The family's measured cadence says not yet.
    RateLimited { wait_ms: u64 },
    /// An unmeasured family needs a person to approve this one operation.
    NeedsConfirmation,
    /// A write was attempted before anything backed the device up.
    NoBackup,
}

/// A transport failure, classified by type and never by message text.
///
/// Recorded because of a finding from TICKET-08: the backend hands back a
/// message with no code, and on Windows that message is the operating system's
/// localised text. Matching on its contents means the kill switch works in
/// English and silently does not in Russian. So `Backend` is one opaque variant
/// here, and nothing in this crate looks inside it.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum FailureKind {
    EndpointStalled,
    NotConnected,
    AccessDenied,
    Backend,
}

impl From<&TransportError> for FailureKind {
    fn from(error: &TransportError) -> Self {
        match error {
            TransportError::EndpointStalled => FailureKind::EndpointStalled,
            TransportError::NotConnected(_) => FailureKind::NotConnected,
            TransportError::AccessDenied => FailureKind::AccessDenied,
            TransportError::Backend(_) => FailureKind::Backend,
        }
    }
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Outcome {
    Completed,
    Refused(Refusal),
    Failed(FailureKind),
    /// The device answered and the answer failed typed validation.
    ///
    /// Distinct from `Completed` and from `Failed` on purpose, and the
    /// distinction is the whole reason the probe path exists. An unsupported
    /// command often replays the previous reply, so "bytes came back" is not
    /// evidence that a command did what it is believed to do. A probe that
    /// produced bytes nobody could make sense of has told us the opposite of
    /// what a completed one tells us, and an entry that recorded both the same
    /// way would be unreadable at exactly the moment it matters.
    Rejected,
}

/// One attempt to reach a device.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct JournalEntry {
    pub at_ms: u64,
    pub device: DeviceId,
    pub family: &'static str,
    pub command: &'static str,
    pub class: OpcodeClass,
    pub intent: Intent,
    /// How many bytes of parameters went with it. The bytes themselves are not
    /// here and are not recoverable from anything that is.
    pub payload_len: usize,
    pub outcome: Outcome,
    pub verification: Verification,
}

/// Where entries go. Supplied by the caller, because the journal store sits
/// above this crate in the dependency graph and this crate must not reach up.
pub trait JournalSink {
    fn record(&mut self, entry: JournalEntry);
}

#[cfg(test)]
mod tests {
    #![allow(clippy::expect_used, clippy::panic, clippy::unwrap_used)]

    use super::*;

    #[test]
    fn an_entry_cannot_carry_payload_bytes() {
        // The seam from spec.md § Test seams, applied to this journal: run a
        // recognisable pattern through the type and look for it in everything
        // the entry can render.
        let entry = JournalEntry {
            at_ms: 1,
            device: DeviceId::new(7),
            family: "royuan-gen2",
            command: "read_led_params",
            class: OpcodeClass::SafeRead,
            intent: Intent::Read,
            payload_len: 8,
            outcome: Outcome::Completed,
            verification: Verification::NotApplicable,
        };
        let rendered = format!("{entry:?}");
        for pattern in ["deadbeef", "DEADBEEF", "222", "0xde"] {
            assert!(
                !rendered.contains(pattern),
                "payload material reached the journal: {rendered}"
            );
        }
        assert!(
            rendered.contains("payload_len: 8"),
            "the length is the part that is useful and safe: {rendered}"
        );
    }

    #[test]
    fn a_transport_failure_is_classified_by_type_not_by_message() {
        // The message is the operating system's localised text. If this ever
        // starts depending on its contents, the kill switch stops working for
        // everyone whose machine is not in English.
        let localised = TransportError::Backend("Ошибка протокола".to_owned());
        assert_eq!(FailureKind::from(&localised), FailureKind::Backend);
        assert_eq!(
            FailureKind::from(&TransportError::EndpointStalled),
            FailureKind::EndpointStalled
        );
    }

    #[test]
    fn intent_follows_the_class() {
        assert_eq!(Intent::from(OpcodeClass::SafeRead), Intent::Read);
        assert_eq!(Intent::from(OpcodeClass::ProbeOk), Intent::Probe);
        assert_eq!(Intent::from(OpcodeClass::BootstrapProbe), Intent::Probe);
        assert_eq!(Intent::from(OpcodeClass::SafeWrite), Intent::Write);
        assert_eq!(Intent::from(OpcodeClass::SlowFlash), Intent::Write);
    }
}
