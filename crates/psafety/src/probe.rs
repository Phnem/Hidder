//! The bootstrap door: how a vendor artifact becomes hardware evidence.
//!
//! # The problem this exists to solve
//!
//! A `safe_read` needs evidence from hardware -- ours, a third party's, or a
//! firmware dump. That rule is right and TICKET-12 was told not to weaken it.
//! But the first command ever sent to a new family has none of those, and the
//! only way to acquire them is to send it once. Without somewhere for that first
//! send to live, the ACL's price of admission is the thing only admission can
//! buy, and the pressure is to add a debug escape hatch, or a raw-opcode path,
//! or a vendor-artifact exception to the read rule. All three are permanent holes
//! bought to solve a temporary problem.
//!
//! So the bootstrap is a first-class concept instead:
//!
//! ```text
//!   vendor artifact
//!        |  reviewed, written into data/protocols/*.toml as bootstrap_probe
//!        v
//!   ProbeCommandId                 a different type from SafeCommandId, so a
//!        |                         probe cannot be run by the production gate
//!        |                         and a production command cannot be run here
//!        v
//!   UserConfirmation               taken by value, one per probe
//!        |
//!        v
//!   ProbeGate::probe(self, ..)     consumes the gate: one authorisation is one
//!        |                         probe, and a series has no way to exist
//!        v
//!   exactly one dispatch           no retry loop, no batch entry point
//!        |
//!        v
//!   ProbeResponse::decode          typed validation; raw bytes never surface
//!        |
//!        v
//!   evidence a human reviews, then a normal ACL entry on `hardware`
//! ```
//!
//! # What this path deliberately does not have
//!
//! - No raw opcode entry point. A caller cannot name bytes; it names a
//!   [`ProbeResponse`] type, and the command comes from that type's associated
//!   constant, which came from the generated table.
//! - No batch. [`ProbeGate::probe`] takes `self` by value, so the gate is gone
//!   afterwards and a second probe needs a second gate and a second
//!   confirmation.
//! - No retry. There is one call to the sink and no loop around it. A probe that
//!   times out has failed; sending it again is a new decision.
//! - No raw response. [`ProbeGate::probe`] returns the decoded type or an error.
//!   Nothing hands back a `Vec<u8>` for a caller to interpret optimistically.
//! - No self-promotion. Nothing here writes to the ACL. A successful probe
//!   produces a value and a journal entry; turning that into `evidence =
//!   "hardware"` is a human editing a reviewed file, followed by a rebuild.

// When the ACL declares no `bootstrap_probe` -- the steady state, and the state
// to prefer -- `ProbeCommandId` has no variants, so it is uninhabited and every
// body in this module that handles one is statically unreachable. That is the
// correct outcome, not dead code to be tidied away: it means nothing in the
// build can run a probe, which is exactly what an empty probe surface should
// mean. The module is still compiled, still type-checked, and still tested
// against the fixture variant.
#![allow(unreachable_code, unused_variables, clippy::diverging_sub_expression)]

use pcaps::{Confidence, FamilyConfidence};
use ptransport::{DeviceId, TransportError};

use crate::class::OpcodeClass;
use crate::gate::Clock;
use crate::journal::{
    FailureKind, Intent, JournalEntry, JournalSink, Outcome, Refusal, Verification,
};
use crate::key::CommandKey;
use crate::rate::UserConfirmation;

/// What the generated table knows about one probe command.
pub(crate) struct ProbeRecord {
    pub(crate) id: ProbeCommandId,
    pub(crate) family: &'static str,
    pub(crate) name: &'static str,
    pub(crate) key: CommandKey,
}

include!(concat!(env!("OUT_DIR"), "/probe_command_id.rs"));

impl ProbeCommandId {
    fn record(self) -> &'static ProbeRecord {
        let index = self as usize;
        // Total by construction: the generated enum numbers its variants from
        // zero in the order of the same table this indexes.
        &PROBES[index]
    }

    pub fn family(self) -> &'static str {
        self.record().family
    }

    pub fn name(self) -> &'static str {
        self.record().name
    }

    /// Always [`OpcodeClass::BootstrapProbe`]. Present so a journal entry does
    /// not have to hardcode it, not because there is a second possibility: the
    /// generator puts nothing else in this enum.
    ///
    /// Note which class this is *not*. `probe_ok` is a production read used to
    /// identify a board, paced by measured timing and earned with hardware
    /// evidence; it generates a [`crate::SafeCommandId`]. This is the class for
    /// a command that has never been sent from here at all.
    pub fn class(self) -> OpcodeClass {
        OpcodeClass::BootstrapProbe
    }

    pub(crate) fn key(self) -> CommandKey {
        self.record().key
    }

    pub fn all() -> impl Iterator<Item = ProbeCommandId> {
        PROBES.iter().map(|record| record.id)
    }

    /// Looks up a probe by family and name. Deliberately not by key, for the
    /// same reason [`crate::SafeCommandId::find`] is not.
    pub fn find(family: &str, name: &str) -> Option<ProbeCommandId> {
        PROBES
            .iter()
            .find(|record| record.family == family && record.name == name)
            .map(|record| record.id)
    }
}

/// A probe that has passed every check and may be put on the wire, once.
///
/// Constructed only by [`ProbeGate`], never `Clone` and never `Copy`, and
/// consumed by value on dispatch -- the same discipline as
/// [`crate::AuthorizedCommand`], and for the same reason. It is a *different
/// type* from that one so that a [`ProbeSink`] cannot be handed a production
/// command and a [`crate::CommandSink`] cannot be handed a probe.
#[derive(Debug)]
pub struct AuthorizedProbe {
    id: ProbeCommandId,
    payload: Vec<u8>,
}

impl AuthorizedProbe {
    fn new(id: ProbeCommandId, payload: Vec<u8>) -> Self {
        Self { id, payload }
    }

    pub fn id(&self) -> ProbeCommandId {
        self.id
    }

    pub fn family(&self) -> &'static str {
        self.id.family()
    }

    pub fn name(&self) -> &'static str {
        self.id.name()
    }

    /// What identifies this command on the wire. From the generated table,
    /// never from the caller.
    pub fn key(&self) -> CommandKey {
        self.id.key()
    }

    /// Parameters for the command. Data, not instructions.
    pub fn payload(&self) -> &[u8] {
        &self.payload
    }
}

/// Where an authorised probe goes.
///
/// Separate from [`crate::CommandSink`] on purpose. A production engine must not
/// double as a general-purpose probe executor: if one object accepted both, the
/// difference between "a verified command" and "a command we are about to find
/// out about" would be a runtime argument rather than a type, and the whole
/// point of the split is that it is not.
pub trait ProbeSink {
    /// Encodes and sends exactly one probe and returns the device's answer.
    ///
    /// The implementor owns the family's codec, so it is what turns a
    /// [`CommandKey`] into a frame. It must not retry, must not send anything
    /// else, and must not treat an unmatched report as the answer.
    fn dispatch_probe(
        &mut self,
        device: DeviceId,
        probe: AuthorizedProbe,
    ) -> Result<Vec<u8>, TransportError>;
}

/// Forwarding impl, so a caller can keep the sink and read a transcript off it
/// after the gate has run. The gate still consumes what it is given -- a `&mut`
/// borrow -- so nothing about one-probe-per-gate changes.
impl<S: ProbeSink + ?Sized> ProbeSink for &mut S {
    fn dispatch_probe(
        &mut self,
        device: DeviceId,
        probe: AuthorizedProbe,
    ) -> Result<Vec<u8>, TransportError> {
        (**self).dispatch_probe(device, probe)
    }
}

/// A typed answer to one probe.
///
/// Implemented per command, by the crate that owns that family's codec. The
/// associated constant is what makes the probe path un-parameterisable by
/// bytes: a caller names this type, and the command comes from the type.
pub trait ProbeResponse: Sized {
    /// Which command this decodes the answer to.
    const COMMAND: ProbeCommandId;

    /// Why an answer was not acceptable. Reported and journalled; never
    /// silently coerced into a value.
    type Rejection: std::fmt::Debug;

    /// The parameters the request carries. Usually fixed for a probe.
    fn request_payload() -> Vec<u8>;

    /// Validates and decodes the device's answer.
    ///
    /// Receives the [`CommandKey`] so it can check the echoed header without
    /// being handed, or having to reconstruct, the request frame. Returning
    /// `Err` is the honest outcome for anything unexpected: this is the one
    /// call standing between "the device sent bytes" and "the command means
    /// what we believed", and an unsupported command replaying a previous reply
    /// is a documented behaviour of boards in this class.
    fn decode(key: CommandKey, response: &[u8]) -> Result<Self, Self::Rejection>;
}

/// Why a probe did not produce a value.
#[derive(Debug)]
pub enum ProbeError<R> {
    /// It never reached the device.
    Refused(Refusal),
    /// The transport failed. If [`TransportError::is_stall`], the caller must
    /// quarantine the device: this crate cannot, because a probe gate holds no
    /// device state by design.
    Transport(TransportError),
    /// The device answered and the answer failed typed validation.
    Rejected(R),
}

impl<R: std::fmt::Debug> std::fmt::Display for ProbeError<R> {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ProbeError::Refused(refusal) => write!(f, "refused: {refusal:?}"),
            ProbeError::Transport(error) => write!(f, "transport: {error}"),
            ProbeError::Rejected(rejection) => write!(f, "rejected: {rejection:?}"),
        }
    }
}

impl<R: std::fmt::Debug> std::error::Error for ProbeError<R> {}

/// Runs exactly one probe, then ceases to exist.
///
/// Holds no device map, no rate limiter and no quarantine state, and that is not
/// an omission. A rate limiter answers "may the next operation go yet", and on
/// this path there is no next operation: [`ProbeGate::probe`] consumes the gate.
/// The cadence a family has never been measured at does not have to be invented
/// here, because nothing is being paced.
pub struct ProbeGate<S: ProbeSink, J: JournalSink, C: Clock> {
    sink: S,
    journal: J,
    clock: C,
}

impl<S: ProbeSink, J: JournalSink, C: Clock> ProbeGate<S, J, C> {
    pub fn new(sink: S, journal: J, clock: C) -> Self {
        Self {
            sink,
            journal,
            clock,
        }
    }

    /// Authorises and runs one probe against one device.
    ///
    /// Consumes the gate. That is the enforcement of "one command per
    /// confirmation": a second probe needs a second gate, built deliberately,
    /// with a second [`UserConfirmation`] that a person had to be asked for.
    ///
    /// `family` and `family_confidence` come from whatever identified the
    /// device. Note what is *not* required: a verified family. Requiring one
    /// would be circular, because verifying the family is what this call is
    /// for. What is required is that something identified the device at all --
    /// an unidentified board is one whose bytes mean nothing known, and there
    /// is no such thing as a safe command to send it.
    pub fn probe<R: ProbeResponse>(
        mut self,
        device: DeviceId,
        family: &'static str,
        family_confidence: FamilyConfidence,
        confirmation: UserConfirmation,
    ) -> Result<R, ProbeError<R::Rejection>> {
        // Consumed here rather than passed on. Nothing downstream may hold it,
        // and it cannot authorise a second operation because it no longer
        // exists: the caller moved it in, and this binding is where it ends.
        let _consumed = confirmation;

        let id = R::COMMAND;
        let now_ms = self.clock.now_ms();
        let payload = R::request_payload();
        let payload_len = payload.len();

        if id.family() != family {
            let refusal = Refusal::WrongFamily {
                device_family: family,
                command_family: id.family(),
            };
            self.write_entry(now_ms, device, id, payload_len, Outcome::Refused(refusal));
            return Err(ProbeError::Refused(refusal));
        }
        if family_confidence.value() == Confidence::Unknown {
            let refusal = Refusal::DeviceNotIdentified;
            self.write_entry(now_ms, device, id, payload_len, Outcome::Refused(refusal));
            return Err(ProbeError::Refused(refusal));
        }

        let key = id.key();
        // One call. No loop, so no retry can be added by adjusting a constant:
        // adding one would mean writing a loop, in a diff someone reviews.
        let result = self
            .sink
            .dispatch_probe(device, AuthorizedProbe::new(id, payload));
        let finished_ms = self.clock.now_ms();

        match result {
            Ok(response) => match R::decode(key, &response) {
                Ok(value) => {
                    self.write_entry(finished_ms, device, id, payload_len, Outcome::Completed);
                    Ok(value)
                }
                Err(rejection) => {
                    self.write_entry(finished_ms, device, id, payload_len, Outcome::Rejected);
                    Err(ProbeError::Rejected(rejection))
                }
            },
            Err(error) => {
                let kind = FailureKind::from(&error);
                self.write_entry(finished_ms, device, id, payload_len, Outcome::Failed(kind));
                Err(ProbeError::Transport(error))
            }
        }
    }

    fn write_entry(
        &mut self,
        at_ms: u64,
        device: DeviceId,
        id: ProbeCommandId,
        payload_len: usize,
        outcome: Outcome,
    ) {
        self.journal.record(JournalEntry {
            at_ms,
            device,
            family: id.family(),
            command: id.name(),
            class: id.class(),
            intent: Intent::Probe,
            payload_len,
            outcome,
            // A probe reads. There is no device state to confirm afterwards,
            // which is exactly why it is allowed to be the first thing sent.
            verification: Verification::NotApplicable,
        });
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::expect_used, clippy::panic, clippy::unwrap_used)]

    use std::cell::{Cell, RefCell};
    use std::rc::Rc;

    use super::*;

    struct FixedClock;

    impl Clock for FixedClock {
        fn now_ms(&self) -> u64 {
            1_000
        }
    }

    #[derive(Default)]
    struct RecordingSink {
        dispatched: Rc<Cell<usize>>,
        seen_key: Rc<Cell<Option<CommandKey>>>,
        answer: Vec<u8>,
        fail_with: Option<TransportError>,
    }

    impl ProbeSink for RecordingSink {
        fn dispatch_probe(
            &mut self,
            _device: DeviceId,
            probe: AuthorizedProbe,
        ) -> Result<Vec<u8>, TransportError> {
            self.dispatched.set(self.dispatched.get() + 1);
            self.seen_key.set(Some(probe.key()));
            match self.fail_with.take() {
                Some(error) => Err(error),
                None => Ok(self.answer.clone()),
            }
        }
    }

    #[derive(Default)]
    struct RecordingJournal {
        entries: Rc<RefCell<Vec<JournalEntry>>>,
    }

    impl JournalSink for RecordingJournal {
        fn record(&mut self, entry: JournalEntry) {
            self.entries.borrow_mut().push(entry);
        }
    }

    /// A probe decoded by a test-local type, so these tests exercise the gate's
    /// mechanics rather than any particular family's codec -- and keep working
    /// when the ACL declares no bootstrap probe at all, which is the state it
    /// should normally be in.
    #[derive(Debug)]
    struct Echoed(Vec<u8>);

    #[derive(Debug, PartialEq, Eq)]
    struct NotEchoed;

    impl ProbeResponse for Echoed {
        const COMMAND: ProbeCommandId = ProbeCommandId::TestFixture;
        type Rejection = NotEchoed;

        fn request_payload() -> Vec<u8> {
            vec![0; 6]
        }

        fn decode(key: CommandKey, response: &[u8]) -> Result<Self, NotEchoed> {
            let header = key.header_bytes();
            if response.starts_with(header.as_slice()) {
                Ok(Echoed(response.to_vec()))
            } else {
                Err(NotEchoed)
            }
        }
    }

    /// The fixture's family. Deliberately not a family the ACL declares, so a
    /// fixture can never be mistaken for a command that could reach a device.
    const FIXTURE_FAMILY: &str = "test-fixture-family";

    fn identified() -> FamilyConfidence {
        FamilyConfidence::established(Confidence::Candidate)
    }

    fn gate(sink: RecordingSink) -> ProbeGate<RecordingSink, RecordingJournal, FixedClock> {
        ProbeGate::new(sink, RecordingJournal::default(), FixedClock)
    }

    #[test]
    fn a_probe_dispatches_the_key_from_the_table_not_from_the_caller() {
        let seen_key = Rc::new(Cell::new(None));
        let sink = RecordingSink {
            seen_key: Rc::clone(&seen_key),
            answer: vec![0x82, 0x01, 0x00],
            ..Default::default()
        };
        gate(sink)
            .probe::<Echoed>(
                DeviceId::new(1),
                FIXTURE_FAMILY,
                identified(),
                UserConfirmation::given(),
            )
            .expect("a well-formed answer decodes");
        assert_eq!(
            seen_key.get(),
            Some(CommandKey::GroupSubcommand {
                group: 0x82,
                subcommand: 0x01
            })
        );
    }

    #[test]
    fn a_probe_for_another_family_is_refused_before_dispatch() {
        let dispatched = Rc::new(Cell::new(0));
        let sink = RecordingSink {
            dispatched: Rc::clone(&dispatched),
            answer: vec![0x82, 0x01],
            ..Default::default()
        };
        let error = gate(sink)
            .probe::<Echoed>(
                DeviceId::new(1),
                "royuan-gen2",
                identified(),
                UserConfirmation::given(),
            )
            .expect_err("a bytech probe must not reach a ROYUAN board");
        assert!(matches!(
            error,
            ProbeError::Refused(Refusal::WrongFamily { .. })
        ));
        assert_eq!(dispatched.get(), 0, "it reached the device anyway");
    }

    #[test]
    fn an_unidentified_device_is_refused() {
        let dispatched = Rc::new(Cell::new(0));
        let sink = RecordingSink {
            dispatched: Rc::clone(&dispatched),
            answer: vec![0x82, 0x01],
            ..Default::default()
        };
        let error = gate(sink)
            .probe::<Echoed>(
                DeviceId::new(1),
                FIXTURE_FAMILY,
                FamilyConfidence::default(),
                UserConfirmation::given(),
            )
            .expect_err("nothing identified this board");
        assert!(matches!(
            error,
            ProbeError::Refused(Refusal::DeviceNotIdentified)
        ));
        assert_eq!(dispatched.get(), 0);
    }

    #[test]
    fn one_gate_dispatches_exactly_once() {
        let dispatched = Rc::new(Cell::new(0));
        let sink = RecordingSink {
            dispatched: Rc::clone(&dispatched),
            answer: vec![0x82, 0x01],
            ..Default::default()
        };
        let gate = gate(sink);
        let _ = gate.probe::<Echoed>(
            DeviceId::new(1),
            FIXTURE_FAMILY,
            identified(),
            UserConfirmation::given(),
        );
        assert_eq!(dispatched.get(), 1);
        // The second call does not compile: `probe` took `self` by value, so
        // `gate` has moved. That is the enforcement, and this comment is where
        // it is recorded, because a test cannot assert on code that does not
        // build. Uncommenting the line below must fail the build:
        //
        //   let _ = gate.probe::<Echoed>(..);
    }

    #[test]
    fn an_answer_that_fails_validation_is_not_a_value() {
        let sink = RecordingSink {
            // A plausible-looking reply to a *different* command: exactly the
            // "unsupported command replays the previous answer" case.
            answer: vec![0x84, 0x11, 0x00],
            ..Default::default()
        };
        let error = gate(sink)
            .probe::<Echoed>(
                DeviceId::new(1),
                FIXTURE_FAMILY,
                identified(),
                UserConfirmation::given(),
            )
            .expect_err("bytes came back, but not to this command");
        assert!(matches!(error, ProbeError::Rejected(NotEchoed)));
    }

    #[test]
    fn a_rejected_answer_is_journalled_as_rejected_not_completed() {
        let entries = Rc::new(RefCell::new(Vec::new()));
        let journal = RecordingJournal {
            entries: Rc::clone(&entries),
        };
        let sink = RecordingSink {
            answer: vec![0x00],
            ..Default::default()
        };
        let _ = ProbeGate::new(sink, journal, FixedClock).probe::<Echoed>(
            DeviceId::new(1),
            FIXTURE_FAMILY,
            identified(),
            UserConfirmation::given(),
        );
        let entries = entries.borrow();
        assert_eq!(entries.len(), 1);
        assert_eq!(entries[0].outcome, Outcome::Rejected);
        assert_eq!(entries[0].intent, Intent::Probe);
        assert_eq!(entries[0].class, OpcodeClass::BootstrapProbe);
    }

    #[test]
    fn a_transport_failure_is_not_retried() {
        let dispatched = Rc::new(Cell::new(0));
        let sink = RecordingSink {
            dispatched: Rc::clone(&dispatched),
            fail_with: Some(TransportError::EndpointStalled),
            ..Default::default()
        };
        let error = gate(sink)
            .probe::<Echoed>(
                DeviceId::new(1),
                FIXTURE_FAMILY,
                identified(),
                UserConfirmation::given(),
            )
            .expect_err("the endpoint stalled");
        match error {
            ProbeError::Transport(error) => assert!(error.is_stall()),
            other => panic!("wrong error: {other:?}"),
        }
        assert_eq!(dispatched.get(), 1, "a probe must never be sent twice");
    }

    #[test]
    fn every_real_probe_is_read_only_and_belongs_to_a_known_family() {
        for id in ProbeCommandId::all() {
            assert_eq!(id.class(), OpcodeClass::BootstrapProbe);
            assert!(!id.class().writes(), "{} is a probe that writes", id.name());
            if id.family() == FIXTURE_FAMILY {
                // The test fixture, which exists in no other build. It names a
                // family the ACL does not declare, which is precisely what stops
                // it from ever matching a device.
                assert!(!crate::known_families().contains(&FIXTURE_FAMILY));
                continue;
            }
            assert!(
                crate::known_families().contains(&id.family()),
                "{} belongs to an unlisted family",
                id.name()
            );
        }
    }

    #[test]
    fn a_probe_is_never_also_a_safe_command() {
        // The two enums are generated from disjoint classes. If a family ever
        // lists the same name twice the generator refuses, so this checks the
        // property that actually matters: no name is executable through both
        // doors at once.
        for id in ProbeCommandId::all() {
            assert!(
                crate::SafeCommandId::find(id.family(), id.name()).is_none(),
                "{}::{} exists on both the probe and the production path",
                id.family(),
                id.name()
            );
        }
    }
}
