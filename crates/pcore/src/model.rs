//! The model the UI reads, in a shape that survives crossing a process boundary.
//!
//! # Why these types exist rather than serialising the ones below
//!
//! Two reasons, and neither is taste.
//!
//! The first is that a UI must not be able to name things it has no business
//! naming. `DiscoveredDevice` carries an OS device path and `DeviceSession`
//! carries the collection it opens; serialising them would put a device path in
//! a web context. Nothing here carries one.
//!
//! The second is that the states this project insists on distinguishing have to
//! survive the trip. "We have no verified command for this", "this board does
//! not have the feature" and "nobody has read it yet" are three different
//! answers (spec.md, domain rules), and `Option<T>` renders all three as `null`.
//! Every such distinction is a variant here, so a front-end that wants to
//! collapse them has to do it deliberately.
//!
//! # What is *not* here
//!
//! Labels a person reads, and the ordering of anything on a screen. A capability
//! arrives as `he.actuation`, not as "Actuation point": the wording is
//! presentation, presentation is the application layer's, and this project ships
//! two languages from its first release (spec.md, non-functional requirements).
//! A front-end that meets an id it does not know shows the id -- which is what
//! prior art requires of its own clients, for the same reason
//! (docs/prior-art/ui-prior-art.md, libratbag).

use pcaps::{CapId, CapValue, Capability, Confidence, Measurement, Origin, Unavailable, Unit};
use pregistry::{FamilyReason, Identification, SignalState};
use psafety::journal::{JournalEntry, Outcome, Verification};
use serde::Serialize;

use crate::session::{DeviceSession, DiscoveredDevice, PresentDevice};

/// One device, as much of it as a card can show.
#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DeviceCard {
    pub id: u64,
    /// `372E:103E`, uppercase, for a person reading a bug report.
    pub hardware_id: String,
    /// Always something readable, even for a device nobody recognises.
    pub label: String,
    pub product: Option<String>,
    pub manufacturer: Option<String>,
    /// Whether a serial number exists. Never its value: the policy is that an
    /// instance identifier is redacted by construction, not on request
    /// (spec.md, domain rules).
    pub serial_present: bool,
    pub identity: IdentityView,
    pub connection: ConnectionView,
    pub capabilities: Vec<CapabilitySlot>,
    pub writes: WriteView,
    /// What the device answered when asked which model it is. Present only once
    /// a session has been established, because until then nothing has asked.
    pub model_id: Option<u64>,
}

/// Three answers, kept apart.
///
/// Never merged into one "how sure are we about this device" number. Knowing
/// exactly which product is plugged in while knowing nothing about its opcode
/// vocabulary is an ordinary state, and averaging the two is how a matcher
/// becomes confidently wrong (TICKET-10, TICKET-22).
#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct IdentityView {
    pub structural: AxisView,
    pub product: AxisView,
    pub family: FamilyView,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AxisView {
    /// What this axis concluded, if anything.
    pub answer: Option<String>,
    /// One of `unknown`, `candidate`, `high`, `verified`.
    pub confidence: &'static str,
    pub signals: Vec<SignalView>,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct FamilyView {
    pub family: Option<String>,
    pub confidence: &'static str,
    /// Why the axis reached that answer, in the matcher's own words.
    pub reason: &'static str,
    /// Whether this confidence would permit a write. One of two conditions --
    /// see [`WriteView`], which is the one a control should be enabled on.
    pub permits_write: bool,
    pub signals: Vec<SignalView>,
}

/// One fingerprint signal and what it did.
///
/// `differed` and `absent` are separate states because they are not the same
/// evidence: a value that disagrees argues against a match, a value that was
/// never reported argues nothing at all.
#[derive(Clone, Copy, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SignalView {
    pub signal: &'static str,
    pub state: &'static str,
}

/// Where a device stands between "plugged in" and "we are talking to it".
#[derive(Clone, Debug, Serialize)]
#[serde(tag = "state", rename_all = "camelCase")]
pub enum ConnectionView {
    /// Enumerated, and nothing on it looks like a channel we know how to speak
    /// on. A complete answer, not a failure: a device this application cannot
    /// configure is a normal thing to find on a bus.
    NoConfigEndpoint,
    /// A recognised configuration channel. Nothing has been sent to it.
    Ready,
    /// A session is open: one command was sent, it answered correctly, and the
    /// protocol family is established by that exchange.
    Connected,
    /// The channel is there and would not open.
    ///
    /// `vendorSoftwareSuspected` is deliberately a suspicion rather than a
    /// finding. The commonest cause is another application holding the endpoint,
    /// and this project's policy is to detect and warn rather than fight for it
    /// (spec.md, compatibility constraints) -- but the backend hands back
    /// localised text with no code, so nothing here can *establish* which cause
    /// applied (TICKET-08). Saying "probably" is the honest width of the claim.
    #[serde(rename_all = "camelCase")]
    Unreachable {
        user_message: String,
        vendor_software_suspected: bool,
    },
    /// The endpoint wedged. The device goes quiet until it is physically
    /// reconnected: reopening does not clear it and further traffic keeps it
    /// pinned, so there is nothing to retry and no retry is offered.
    #[serde(rename_all = "camelCase")]
    Stalled { user_message: String },
}

/// One capability and whether it can be read here.
#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CapabilitySlot {
    /// The canonical id, e.g. `he.actuation`.
    pub id: &'static str,
    pub availability: AvailabilityView,
}

/// Why a capability has no value, spelled out.
///
/// The whole point of not being a boolean: a feature this board lacks and a
/// feature this build cannot ask about look identical to a user unless something
/// insists on the difference, and only one of them is ever going to change by
/// installing an update.
#[derive(Clone, Debug, Serialize)]
#[serde(tag = "state", rename_all = "camelCase")]
pub enum AvailabilityView {
    /// A verified command exists for this device's family. It can be read now.
    Readable,
    /// Readable, not read yet.
    NotRead,
    /// This project has no verified command for it on this family. Says nothing
    /// about whether the hardware has the feature.
    NoVerifiedCommand,
    /// Established, with evidence, that this device does not have it.
    #[serde(rename_all = "camelCase")]
    NotPresent { evidence: String },
}

impl From<Unavailable> for AvailabilityView {
    fn from(reason: Unavailable) -> Self {
        match reason {
            Unavailable::NotRead => AvailabilityView::NotRead,
            Unavailable::NoVerifiedCommand => AvailabilityView::NoVerifiedCommand,
            Unavailable::NotPresent { evidence } => AvailabilityView::NotPresent { evidence },
        }
    }
}

/// Whether this device can be written to, and the two separate reasons it
/// cannot.
///
/// Both numbers travel, because either one alone misleads. A build whose family
/// is `Verified` but which has no write command in its ACL would report
/// "permitted" from the first field and be unable to write anything.
#[derive(Clone, Copy, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct WriteView {
    /// The protocol-family axis reached `verified`.
    pub family_permits: bool,
    /// How many write commands exist for this family in this build. Comes from
    /// the generated ACL, so it stops being zero exactly when a reviewed
    /// `safe_write` entry is added and not a moment before.
    pub commands_available: usize,
    /// Both conditions. The only field a write control may be enabled on.
    pub enabled: bool,
}

/// A capability that was read, with everything needed to judge it.
#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CapabilityReading {
    pub id: &'static str,
    /// `verified on hardware`, `from a vendor artifact`, or `assumed`.
    pub origin: &'static str,
    /// Which command earned it, when one did.
    pub origin_command: Option<&'static str>,
    /// Whether this may be presented as a fact about the device rather than as
    /// an expectation. The flag a control's enabled state is allowed to depend
    /// on; the string above is for a person to read.
    pub from_hardware: bool,
    pub confidence: &'static str,
    /// Why this value means what it says, in one line.
    pub provenance: String,
    pub value: ValueView,
    pub read_at_unix_ms: u64,
}

#[derive(Clone, Debug, Serialize)]
#[serde(tag = "shape", rename_all = "camelCase")]
pub enum ValueView {
    #[serde(rename_all = "camelCase")]
    Scalar { measurement: MeasurementView },
    #[serde(rename_all = "camelCase")]
    PerKey { keys: Vec<KeyMeasurementView> },
    #[serde(rename_all = "camelCase")]
    Compound { entries: Vec<KeyMeasurementView> },
    /// A continuous feed, carrying no samples. Samples arrive on their own
    /// channel and a snapshot of a stream would be a different thing wearing its
    /// name (TICKET-15).
    #[serde(rename_all = "camelCase")]
    Stream { unit: &'static str },
}

/// A number, its unit, and the way it should be shown.
///
/// `rendered` travels alongside the raw value on purpose. How many decimals a
/// number deserves is derived from the device's own step, and a front-end
/// formatting the float itself would invent digits the hardware cannot
/// distinguish -- which this project has already had happen once, to a scale
/// nobody had written down.
#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct MeasurementView {
    pub value: f64,
    pub unit: &'static str,
    pub decimals: u8,
    pub rendered: String,
}

impl From<Measurement> for MeasurementView {
    fn from(measurement: Measurement) -> Self {
        Self {
            value: measurement.value,
            unit: unit_str(measurement.unit),
            decimals: measurement.decimals,
            rendered: measurement.render(),
        }
    }
}

/// One key's value, keyed by a label rather than by a protocol key id.
///
/// The label is what the layer below chose to call the key. A vendor key id
/// would oblige the UI to hold the vendor's key table to render it, which is the
/// coupling the capability layer exists to prevent -- and this project has
/// already lost one hardware exchange to two key-numbering spaces being treated
/// as interchangeable.
#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct KeyMeasurementView {
    pub label: String,
    pub measurement: MeasurementView,
}

/// One row of the journal.
///
/// Carries `payloadLen` and no payload, because that is what the entry it comes
/// from carries. A payload can hold a keymap, a keymap can hold a macro, and a
/// macro can hold a password; this record is built to be attachable to a bug
/// report without anyone reviewing it first.
#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct JournalRow {
    /// Wall-clock time, for a person. Derived from the monotonic offset below
    /// plus the time the process's clock was zeroed, so it never jumps backwards
    /// relative to the other rows even if the system clock does.
    pub at_unix_ms: u64,
    /// Milliseconds since this process's clock was zeroed. The ordering
    /// authority; two rows are comparable by this and only by this.
    pub at_ms: u64,
    pub device_id: u64,
    pub family: &'static str,
    pub command: &'static str,
    /// The ACL class the command was authorised under: `safe_read`,
    /// `bootstrap_probe`, and so on.
    pub class: &'static str,
    /// `read`, `probe` or `write`, in the vocabulary a person understands.
    pub intent: &'static str,
    pub payload_len: usize,
    /// `ok`, `refused`, `failed` or `rejected`. Four states rather than a
    /// boolean: an answer that failed validation has told us the opposite of
    /// what a completed one tells us, and a record that showed both as failure
    /// would be unreadable exactly when it matters.
    pub outcome: &'static str,
    /// What was refused or what failed, when something was.
    pub detail: Option<String>,
    pub verification: &'static str,
    /// The line as this project renders it, so two front-ends cannot describe
    /// the same event differently in two bug reports.
    pub line: String,
}

// --- construction -------------------------------------------------------------

impl DeviceCard {
    /// The card for a device that has been identified but not spoken to.
    pub fn from_discovered(discovered: &DiscoveredDevice) -> Self {
        let present: &PresentDevice = &discovered.present;
        Self {
            id: discovered.device().raw(),
            hardware_id: format!("{:04X}:{:04X}", present.vendor_id, present.product_id),
            label: present.label(),
            product: present.product.clone(),
            manufacturer: present.manufacturer.clone(),
            serial_present: present.serial_present,
            identity: identity_view(&discovered.identification),
            connection: if present.is_connectable() {
                ConnectionView::Ready
            } else {
                ConnectionView::NoConfigEndpoint
            },
            capabilities: CapId::all()
                .iter()
                .map(|id| CapabilitySlot {
                    id: id.as_str(),
                    availability: match discovered.capability_state(*id) {
                        Ok(()) => AvailabilityView::Readable,
                        Err(reason) => reason.into(),
                    },
                })
                .collect(),
            writes: WriteView {
                family_permits: discovered.identification.permits_write(),
                commands_available: 0,
                enabled: false,
            },
            model_id: None,
        }
    }

    /// Replaces the identification-derived half of a card with what a session
    /// established.
    ///
    /// Applied rather than rebuilt so that everything enumeration knows -- the
    /// strings, the serial flag -- stays exactly as it was. A connect must not be
    /// able to quietly change what the device is called.
    pub fn with_session(mut self, session: &DeviceSession) -> Self {
        self.identity = identity_view(session.identification());
        self.connection = ConnectionView::Connected;
        self.capabilities = session
            .capabilities()
            .into_iter()
            .map(|(id, state)| CapabilitySlot {
                id: id.as_str(),
                availability: match state {
                    Ok(()) => AvailabilityView::Readable,
                    Err(reason) => reason.into(),
                },
            })
            .collect();
        self.writes = WriteView {
            family_permits: session.family_permits_write(),
            commands_available: session.writable_commands(),
            enabled: session.is_writable(),
        };
        self.model_id = Some(session.model_id());
        self
    }
}

fn identity_view(identification: &Identification) -> IdentityView {
    IdentityView {
        structural: AxisView {
            answer: Some(identification.structural.id.to_string()),
            confidence: identification.structural.confidence.as_str(),
            signals: signal_views(&identification.structural.signals),
        },
        product: AxisView {
            answer: identification.product.entry.map(str::to_owned),
            confidence: identification.product.confidence.as_str(),
            signals: signal_views(&identification.product.signals),
        },
        family: FamilyView {
            family: identification.family.family.map(str::to_owned),
            confidence: identification.family.confidence.as_str(),
            reason: family_reason_str(identification.family.reason),
            permits_write: identification.permits_write(),
            signals: signal_views(&identification.family.signals),
        },
    }
}

fn signal_views(signals: &[pregistry::SignalOutcome]) -> Vec<SignalView> {
    signals
        .iter()
        .map(|outcome| SignalView {
            signal: outcome.signal.as_str(),
            state: match outcome.state {
                SignalState::Matched => "matched",
                SignalState::Differed => "differed",
                SignalState::Absent => "absent",
            },
        })
        .collect()
}

fn family_reason_str(reason: FamilyReason) -> &'static str {
    match reason {
        FamilyReason::NoEvidence => "no_evidence",
        FamilyReason::NotRecorded => "not_recorded",
        FamilyReason::FromRegistry => "from_registry",
        FamilyReason::FromProtocolEvidence => "from_protocol_evidence",
    }
}

fn unit_str(unit: Unit) -> &'static str {
    match unit {
        Unit::Millimetres => Unit::Millimetres.suffix(),
    }
}

impl CapabilityReading {
    pub fn new(capability: &Capability, read_at_unix_ms: u64) -> Self {
        Self {
            id: capability.id.as_str(),
            origin: capability.origin.as_str(),
            origin_command: match capability.origin {
                Origin::VerifiedOnHardware { command } => Some(command),
                Origin::VendorArtifact | Origin::Assumed => None,
            },
            from_hardware: capability.origin.is_from_hardware(),
            confidence: confidence_str(capability.confidence),
            provenance: capability.provenance.clone(),
            value: value_view(&capability.value),
            read_at_unix_ms,
        }
    }
}

fn confidence_str(confidence: Confidence) -> &'static str {
    confidence.as_str()
}

fn value_view(value: &CapValue) -> ValueView {
    match value {
        CapValue::Scalar(measurement) => ValueView::Scalar {
            measurement: (*measurement).into(),
        },
        CapValue::PerKey(keys) => ValueView::PerKey {
            keys: keys
                .iter()
                .map(|key| KeyMeasurementView {
                    label: key.label.clone(),
                    measurement: key.measurement.into(),
                })
                .collect(),
        },
        CapValue::Compound(entries) => ValueView::Compound {
            entries: entries
                .iter()
                .map(|(name, measurement)| KeyMeasurementView {
                    label: (*name).to_owned(),
                    measurement: (*measurement).into(),
                })
                .collect(),
        },
        CapValue::Stream { unit } => ValueView::Stream {
            unit: unit_str(*unit),
        },
    }
}

impl JournalRow {
    pub fn new(entry: &JournalEntry, epoch_unix_ms: u64) -> Self {
        let (outcome, detail) = match entry.outcome {
            Outcome::Completed => ("ok", None),
            Outcome::Rejected => (
                "rejected",
                Some("the device answered and the answer failed validation".to_owned()),
            ),
            Outcome::Refused(refusal) => ("refused", Some(format!("{refusal:?}"))),
            Outcome::Failed(kind) => ("failed", Some(format!("{kind:?}"))),
        };
        Self {
            at_unix_ms: epoch_unix_ms.saturating_add(entry.at_ms),
            at_ms: entry.at_ms,
            device_id: entry.device.raw(),
            family: entry.family,
            command: entry.command,
            class: entry.class.as_str(),
            intent: match entry.intent {
                psafety::journal::Intent::Read => "read",
                psafety::journal::Intent::Probe => "probe",
                psafety::journal::Intent::Write => "write",
            },
            payload_len: entry.payload_len,
            outcome,
            detail,
            verification: match entry.verification {
                Verification::NotApplicable => "not_applicable",
                Verification::Pending => "pending",
                Verification::Confirmed { .. } => "confirmed",
                Verification::Contradicted => "contradicted",
            },
            line: pjournal::render_entry(entry),
        }
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::expect_used, clippy::panic, clippy::unwrap_used)]

    use super::*;
    use pcaps::{CapValue, KeyMeasurement, Unit};
    use psafety::OpcodeClass;
    use psafety::journal::{Intent, Refusal};
    use ptransport::DeviceId;

    fn actuation(raw_mm: f64) -> Capability {
        Capability {
            id: CapId::HeActuation,
            origin: Origin::VerifiedOnHardware {
                command: "read_key_travel",
            },
            confidence: Confidence::Verified,
            value: CapValue::PerKey(vec![KeyMeasurement {
                label: "W".to_owned(),
                measurement: Measurement::new(raw_mm, Unit::Millimetres, 2),
            }]),
            provenance: "read with command read_key_travel".to_owned(),
        }
    }

    #[test]
    fn a_reading_carries_the_rendering_and_not_only_the_number() {
        // The failure this prevents: a front-end formatting 0.51 with whatever
        // precision it likes, on a device whose step is 0.01 mm. How many
        // decimals mean anything is a fact about the hardware, so it is decided
        // here and shipped with the value.
        let reading = CapabilityReading::new(&actuation(0.51), 1_000);
        let ValueView::PerKey { keys } = &reading.value else {
            panic!("actuation is per-key");
        };
        assert_eq!(keys[0].label, "W");
        assert_eq!(keys[0].measurement.rendered, "0.51 mm");
        assert_eq!(keys[0].measurement.decimals, 2);
        assert_eq!(keys[0].measurement.unit, "mm");
    }

    #[test]
    fn a_reading_names_the_command_that_earned_it() {
        let reading = CapabilityReading::new(&actuation(0.51), 0);
        assert!(reading.from_hardware);
        assert_eq!(reading.origin_command, Some("read_key_travel"));
        assert_eq!(reading.origin, "verified on hardware");
    }

    #[test]
    fn an_assumed_origin_is_not_from_hardware_and_names_no_command() {
        // What a control's enabled state is allowed to depend on. If this ever
        // reports `from_hardware` for an assumption, a UI obeying the design
        // rule would enable a control with nothing behind it.
        let mut capability = actuation(0.51);
        capability.origin = Origin::Assumed;
        capability.confidence = Confidence::Candidate;
        let reading = CapabilityReading::new(&capability, 0);
        assert!(!reading.from_hardware);
        assert_eq!(reading.origin_command, None);
        assert_eq!(reading.confidence, "candidate");
    }

    fn entry(outcome: Outcome) -> JournalEntry {
        JournalEntry {
            at_ms: 250,
            device: DeviceId::new(0x372E_103E),
            family: "aula-bytech",
            command: "read_key_travel",
            class: OpcodeClass::SafeRead,
            intent: Intent::Read,
            payload_len: 8,
            outcome,
            verification: Verification::NotApplicable,
        }
    }

    #[test]
    fn a_refusal_reaches_the_row_as_a_refusal_and_says_which_one() {
        // A journal that showed only what succeeded could not answer the
        // question it exists for. The refusal reason has to survive to the row,
        // or the screen shows a failure with no cause.
        let row = JournalRow::new(
            &entry(Outcome::Refused(Refusal::NoBackup)),
            1_700_000_000_000,
        );
        assert_eq!(row.outcome, "refused");
        assert_eq!(row.detail.as_deref(), Some("NoBackup"));
        assert_eq!(row.at_unix_ms, 1_700_000_000_250);
        assert_eq!(row.at_ms, 250);
    }

    #[test]
    fn an_answer_that_failed_validation_is_not_recorded_as_a_success() {
        let row = JournalRow::new(&entry(Outcome::Rejected), 0);
        assert_eq!(row.outcome, "rejected");
        assert_ne!(row.outcome, "ok");
    }

    #[test]
    fn no_payload_bytes_reach_a_row() {
        // The same seam pjournal holds, applied at the boundary the UI reads
        // from: run a recognisable pattern through and look for it in
        // everything the row can render.
        let row = JournalRow::new(&entry(Outcome::Completed), 0);
        let rendered = format!("{row:?}");
        for pattern in ["deadbeef", "DEADBEEF", "0xde"] {
            assert!(!rendered.contains(pattern), "payload material: {rendered}");
        }
        assert_eq!(row.payload_len, 8);
        assert!(row.line.contains("read_key_travel"));
    }
}
