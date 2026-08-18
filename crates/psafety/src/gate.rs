//! The one door.

use std::collections::HashMap;

use pcaps::FamilyConfidence;
use ptransport::{DeviceId, TransportError};

use crate::class::OpcodeClass;
use crate::command::{AuthorizedCommand, SafeCommandId};
use crate::journal::{
    FailureKind, Intent, JournalEntry, JournalSink, Outcome, Refusal, Verification,
};
use crate::key::CommandKey;
use crate::rate::{RateDecision, RateLimiter, UserConfirmation};

/// Where an authorised command goes once every check has passed.
///
/// Implemented in TICKET-12 by the layer that owns device sessions. Note which
/// way round this is: the sink holds the sessions and this crate does not. A
/// gate that owned a `SessionHandle` would be a second place device ownership
/// lives, and there is supposed to be exactly one (spec.md FR10).
///
/// The implementor receives an [`AuthorizedCommand`] and cannot construct one,
/// so it has nothing to dispatch that the gate did not approve, and nothing to
/// replay: the value is consumed.
pub trait CommandSink {
    fn dispatch(
        &mut self,
        device: DeviceId,
        command: AuthorizedCommand,
    ) -> Result<Vec<u8>, TransportError>;
}

/// Forwarding impl, so a caller can retain the sink -- to read a transcript off
/// it, typically -- while the gate borrows it. Authorisation is unaffected: the
/// gate still mints the command and still consumes it on dispatch.
impl<S: CommandSink + ?Sized> CommandSink for &mut S {
    fn dispatch(
        &mut self,
        device: DeviceId,
        command: AuthorizedCommand,
    ) -> Result<Vec<u8>, TransportError> {
        (**self).dispatch(device, command)
    }
}

/// Monotonic time in milliseconds. Injected so the cadence tests are about
/// cadence rather than about sleeping.
pub trait Clock {
    fn now_ms(&self) -> u64;
}

/// The process clock, started when the gate is built.
pub struct MonotonicClock {
    start: std::time::Instant,
}

impl Default for MonotonicClock {
    fn default() -> Self {
        Self {
            start: std::time::Instant::now(),
        }
    }
}

impl Clock for MonotonicClock {
    fn now_ms(&self) -> u64 {
        // Monotonic on every supported platform, so this never goes backwards
        // and a cooldown cannot be skipped by changing the system clock.
        u64::try_from(self.start.elapsed().as_millis()).unwrap_or(u64::MAX)
    }
}

/// A typed answer to one production read.
///
/// The read-path twin of [`crate::ProbeResponse`], and it exists for the same
/// reason: so that a caller names a *type*, the command comes from that type's
/// associated constant, and the bytes that identify the command never pass
/// through the caller's hands. An engine holding this trait can ask for a model
/// id; it cannot ask for `0x82:0x01`.
pub trait CommandResponse: Sized {
    /// Which command this decodes the answer to.
    const COMMAND: SafeCommandId;

    /// Why an answer was not acceptable. Reported and journalled; never
    /// silently coerced into a value.
    type Rejection: std::fmt::Debug;

    /// The parameters the request carries.
    fn request_payload() -> Vec<u8>;

    /// Validates and decodes the device's answer.
    ///
    /// Receives the [`CommandKey`] so it can check an echoed header without
    /// being handed the request frame. Returning `Err` is the honest outcome for
    /// anything unexpected: an unsupported command replaying a previous reply is
    /// documented behaviour on boards in this class, so "bytes came back" is not
    /// evidence that the command did what it is believed to do.
    fn decode(key: CommandKey, response: &[u8]) -> Result<Self, Self::Rejection>;
}

#[derive(Debug)]
pub enum SafetyError {
    Refused(Refusal),
    Transport(TransportError),
}

/// Why a typed read did not produce a value.
#[derive(Debug)]
pub enum ReadError<R> {
    Refused(Refusal),
    Transport(TransportError),
    /// The device answered and the answer failed typed validation.
    Rejected(R),
}

impl<R: std::fmt::Debug> std::fmt::Display for ReadError<R> {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ReadError::Refused(refusal) => write!(f, "refused: {refusal:?}"),
            ReadError::Transport(error) => write!(f, "transport: {error}"),
            ReadError::Rejected(rejection) => write!(f, "rejected: {rejection:?}"),
        }
    }
}

impl<R: std::fmt::Debug> std::error::Error for ReadError<R> {}

impl std::fmt::Display for SafetyError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            SafetyError::Refused(refusal) => write!(f, "refused: {refusal:?}"),
            SafetyError::Transport(error) => write!(f, "transport: {error}"),
        }
    }
}

impl std::error::Error for SafetyError {}

/// Whether anything has backed this device up yet.
///
/// The gate knows only that a backup exists, never what is in one. What a backup
/// contains, and how it is restored, is a protocol question that the first
/// read-only engine cannot answer and TICKET-15 has to: inventing a shape for it
/// now would mean inventing a restore path nothing can perform.
#[derive(Clone, Copy, PartialEq, Eq, Debug, Default)]
pub enum BackupState {
    #[default]
    None,
    Recorded {
        at_ms: u64,
    },
}

#[derive(Debug)]
struct DeviceState {
    family: &'static str,
    /// How well established it is that this device speaks that family. Supplied
    /// by whatever identified the device; this crate never derives it, and its
    /// type makes a confidence from another axis unusable here.
    family_confidence: FamilyConfidence,
    backup: BackupState,
    quarantined: bool,
}

/// The only path from an intent to a byte on a wire.
///
/// Owns the sink rather than lending it out. An engine holds a `&mut SafetyGate`
/// and calls [`SafetyGate::execute`]; it never holds the sink, so there is no
/// object it could dispatch through directly. Together with the fact that
/// [`AuthorizedCommand`] has no public constructor, that is the boundary: an
/// engine cannot express "send this byte", only "run this approved command".
pub struct SafetyGate<S: CommandSink, J: JournalSink, C: Clock = MonotonicClock> {
    sink: S,
    journal: J,
    clock: C,
    limiter: RateLimiter,
    devices: HashMap<DeviceId, DeviceState>,
}

impl<S: CommandSink, J: JournalSink, C: Clock> SafetyGate<S, J, C> {
    pub fn new(sink: S, journal: J, clock: C) -> Self {
        Self {
            sink,
            journal,
            clock,
            limiter: RateLimiter::new(),
            devices: HashMap::new(),
        }
    }

    /// Tells the gate what family a device speaks, and how well established
    /// that is.
    ///
    /// Called by whatever identified the device -- `pregistry`. Until it is
    /// called, the device is unidentified and every command is refused, which
    /// is the default-deny position: an unidentified board is one whose opcodes
    /// mean something unknown.
    ///
    /// `family_confidence` must be the confidence of the **protocol-family**
    /// axis specifically, not of the identification as a whole. There is no
    /// such thing as the confidence of an identification: knowing exactly which
    /// product is plugged in while knowing nothing about its opcode vocabulary
    /// is a normal state, and it is the second fact that decides whether a
    /// write may happen (spec.md § Domain rules; TICKET-22 for the hardware that
    /// made the distinction unavoidable).
    pub fn identify_device(
        &mut self,
        device: DeviceId,
        family: &'static str,
        family_confidence: FamilyConfidence,
    ) {
        self.devices.insert(
            device,
            DeviceState {
                family,
                family_confidence,
                backup: BackupState::None,
                quarantined: false,
            },
        );
    }

    /// The protocol-family confidence recorded for a device, if any.
    pub fn family_confidence(&self, device: DeviceId) -> FamilyConfidence {
        self.devices
            .get(&device)
            .map(|state| state.family_confidence)
            .unwrap_or_default()
    }

    /// Records that a backup of this device exists. Required before any write.
    pub fn record_backup(&mut self, device: DeviceId) {
        let at_ms = self.clock.now_ms();
        if let Some(state) = self.devices.get_mut(&device) {
            state.backup = BackupState::Recorded { at_ms };
        }
    }

    pub fn backup_state(&self, device: DeviceId) -> BackupState {
        self.devices
            .get(&device)
            .map(|state| state.backup)
            .unwrap_or_default()
    }

    pub fn is_quarantined(&self, device: DeviceId) -> bool {
        self.devices
            .get(&device)
            .map(|state| state.quarantined)
            .unwrap_or(false)
    }

    /// Clears a quarantine. The only thing that does.
    ///
    /// Named after the physical act because that is the requirement: a stalled
    /// endpoint does not recover from being reopened, and traffic keeps it
    /// stalled. The caller is expected to have observed the device leave and
    /// come back, not merely to have decided enough time has passed.
    pub fn device_reconnected(&mut self, device: DeviceId) {
        self.limiter.forget(device);
        if let Some(state) = self.devices.get_mut(&device) {
            state.quarantined = false;
            // A backup taken before a reconnect describes the same device, so it
            // stands. What does not stand is the cadence: the endpoint was
            // reset, so its history is not informative.
        }
    }

    pub fn forget_device(&mut self, device: DeviceId) {
        self.devices.remove(&device);
        self.limiter.forget(device);
    }

    /// Runs one approved command against one device.
    ///
    /// One command per call, by design. There is no batch entry point, because a
    /// batch is exactly what a caller would reach for to drive a series past a
    /// rate limit.
    pub fn execute(
        &mut self,
        device: DeviceId,
        id: SafeCommandId,
        payload: Vec<u8>,
        confirmation: Option<UserConfirmation>,
    ) -> Result<Vec<u8>, SafetyError> {
        let now_ms = self.clock.now_ms();
        let class = id.class();

        if let Err(refusal) =
            self.authorize(device, id.family(), id.name(), class, now_ms, confirmation)
        {
            self.write_entry(now_ms, device, id, payload.len(), Outcome::Refused(refusal));
            return Err(SafetyError::Refused(refusal));
        }

        let command = AuthorizedCommand::new(id, payload);
        let payload_len = command.payload().len();
        let result = self.sink.dispatch(device, command);

        // The operation happened, so its quiet period starts, whether or not it
        // succeeded. A write that failed still touched the device.
        let finished_ms = self.clock.now_ms();
        self.limiter
            .record(device, id.family(), Some(id.name()), class, finished_ms);

        match result {
            Ok(answer) => {
                self.write_entry(finished_ms, device, id, payload_len, Outcome::Completed);
                Ok(answer)
            }
            Err(error) => {
                let kind = FailureKind::from(&error);
                // The kill switch. Only the typed stall trips it: classifying a
                // backend message by its text is what TICKET-08 established
                // cannot be done, so a stall the transport failed to type is a
                // gap in the transport, not something to guess at here.
                //
                // There is no retry anywhere on this path, and no reopen.
                if kind == FailureKind::EndpointStalled
                    && let Some(state) = self.devices.get_mut(&device)
                {
                    state.quarantined = true;
                }
                self.write_entry(finished_ms, device, id, payload_len, Outcome::Failed(kind));
                Err(SafetyError::Transport(error))
            }
        }
    }

    /// Runs one approved read and decodes its answer.
    ///
    /// Every check [`SafetyGate::execute`] performs, plus typed validation of
    /// the answer, and no raw bytes in either direction: the payload comes from
    /// the response type and the answer leaves as that type or as an error.
    ///
    /// A rejected answer is journalled as [`Outcome::Rejected`] rather than as
    /// a completion, because a reply that could not be made sense of is the
    /// opposite of a successful read and an entry that recorded both the same
    /// way would be unreadable exactly when it mattered.
    pub fn read<R: CommandResponse>(
        &mut self,
        device: DeviceId,
        confirmation: Option<UserConfirmation>,
    ) -> Result<R, ReadError<R::Rejection>> {
        let id = R::COMMAND;
        let payload = R::request_payload();
        let payload_len = payload.len();
        let key = id.key();

        match self.execute(device, id, payload, confirmation) {
            Ok(answer) => match R::decode(key, &answer) {
                Ok(value) => Ok(value),
                Err(rejection) => {
                    // `execute` already journalled the completion; this second
                    // entry records that the bytes did not survive validation.
                    let at_ms = self.clock.now_ms();
                    self.write_entry(at_ms, device, id, payload_len, Outcome::Rejected);
                    Err(ReadError::Rejected(rejection))
                }
            },
            Err(SafetyError::Refused(refusal)) => Err(ReadError::Refused(refusal)),
            Err(SafetyError::Transport(error)) => Err(ReadError::Transport(error)),
        }
    }

    /// Every check, in the order that refuses earliest.
    fn authorize(
        &self,
        device: DeviceId,
        command_family: &'static str,
        command_name: &'static str,
        class: OpcodeClass,
        now_ms: u64,
        confirmation: Option<UserConfirmation>,
    ) -> Result<(), Refusal> {
        let Some(state) = self.devices.get(&device) else {
            return Err(Refusal::DeviceNotIdentified);
        };
        if state.quarantined {
            return Err(Refusal::Quarantined);
        }
        if state.family != command_family {
            return Err(Refusal::WrongFamily {
                device_family: state.family,
                command_family,
            });
        }
        // Writes need the family established, not merely the device recognised.
        // Reads are deliberately unaffected: a device we cannot write to still
        // opens read-only rather than not at all.
        if class.writes() && !state.family_confidence.permits_write() {
            return Err(Refusal::UnverifiedFamily {
                confidence: state.family_confidence,
            });
        }
        if class.needs_backup() && state.backup == BackupState::None {
            return Err(Refusal::NoBackup);
        }
        match self.limiter.check(
            device,
            command_family,
            Some(command_name),
            class,
            now_ms,
            confirmation,
        ) {
            RateDecision::Allow => Ok(()),
            RateDecision::Wait { ms, .. } => Err(Refusal::RateLimited { wait_ms: ms }),
            RateDecision::NeedsConfirmation => Err(Refusal::NeedsConfirmation),
        }
    }

    fn write_entry(
        &mut self,
        at_ms: u64,
        device: DeviceId,
        id: SafeCommandId,
        payload_len: usize,
        outcome: Outcome,
    ) {
        let class = id.class();
        let verification = match (class.writes(), outcome) {
            (false, _) => Verification::NotApplicable,
            // A write that completed is a write that has not been confirmed.
            // The device's answer is not evidence, so nothing here may upgrade
            // this; TICKET-15 owns the confirmation step.
            (true, Outcome::Completed) => Verification::Pending,
            (true, _) => Verification::NotApplicable,
        };
        self.journal.record(JournalEntry {
            at_ms,
            device,
            family: id.family(),
            command: id.name(),
            class,
            intent: Intent::from(class),
            payload_len,
            outcome,
            verification,
        });
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::expect_used, clippy::panic, clippy::unwrap_used)]

    use std::cell::Cell;
    use std::rc::Rc;

    use pcaps::{Confidence, FamilyConfidence};

    use super::*;

    #[derive(Default)]
    struct TestClock {
        now: Rc<Cell<u64>>,
    }

    impl Clock for TestClock {
        fn now_ms(&self) -> u64 {
            self.now.get()
        }
    }

    #[derive(Default)]
    struct RecordingSink {
        dispatched: Rc<Cell<usize>>,
        seen_key: Rc<Cell<Option<crate::key::CommandKey>>>,
        fail_with: Option<TransportError>,
    }

    impl CommandSink for RecordingSink {
        fn dispatch(
            &mut self,
            _device: DeviceId,
            command: AuthorizedCommand,
        ) -> Result<Vec<u8>, TransportError> {
            self.dispatched.set(self.dispatched.get() + 1);
            self.seen_key.set(Some(command.key()));
            match self.fail_with.take() {
                Some(error) => Err(error),
                None => Ok(command.key().header_bytes().to_vec()),
            }
        }
    }

    #[derive(Default)]
    struct RecordingJournal {
        entries: Rc<std::cell::RefCell<Vec<JournalEntry>>>,
    }

    impl JournalSink for RecordingJournal {
        fn record(&mut self, entry: JournalEntry) {
            self.entries.borrow_mut().push(entry);
        }
    }

    struct Harness {
        gate: SafetyGate<RecordingSink, RecordingJournal, TestClock>,
        now: Rc<Cell<u64>>,
        dispatched: Rc<Cell<usize>>,
        seen_key: Rc<Cell<Option<crate::key::CommandKey>>>,
        entries: Rc<std::cell::RefCell<Vec<JournalEntry>>>,
        device: DeviceId,
    }

    fn harness(fail_with: Option<TransportError>) -> Harness {
        let now = Rc::new(Cell::new(1_000));
        let dispatched = Rc::new(Cell::new(0));
        let seen_key = Rc::new(Cell::new(None));
        let entries = Rc::new(std::cell::RefCell::new(Vec::new()));
        let gate = SafetyGate::new(
            RecordingSink {
                dispatched: Rc::clone(&dispatched),
                seen_key: Rc::clone(&seen_key),
                fail_with,
            },
            RecordingJournal {
                entries: Rc::clone(&entries),
            },
            TestClock {
                now: Rc::clone(&now),
            },
        );
        Harness {
            gate,
            now,
            dispatched,
            seen_key,
            entries,
            device: DeviceId::new(1),
        }
    }

    fn identify_command() -> SafeCommandId {
        SafeCommandId::find("royuan-gen2", "identify").expect("the ACL ships this command")
    }

    // --- default deny -----------------------------------------------------

    #[test]
    fn an_unidentified_device_can_run_nothing() {
        let mut h = harness(None);
        let error = h
            .gate
            .execute(h.device, identify_command(), Vec::new(), None)
            .expect_err("a board nobody has identified is a board of unknown opcodes");
        assert!(matches!(
            error,
            SafetyError::Refused(Refusal::DeviceNotIdentified)
        ));
        assert_eq!(h.dispatched.get(), 0, "it reached the transport anyway");
    }

    #[test]
    fn a_command_from_another_family_is_refused() {
        // The documented collision: on yc500 the byte 0x06 is Options, with a
        // full-keyboard lock bit in it, and on gen2 the same byte is Debounce.
        // A gate that only checked "is this command approved" would send one
        // family's approved byte to the other family's board.
        let mut h = harness(None);
        h.gate.identify_device(
            h.device,
            "royuan-yc500",
            FamilyConfidence::established(Confidence::Verified),
        );
        let gen2 = SafeCommandId::find("royuan-gen2", "read_led_params").expect("ships");

        let error = h
            .gate
            .execute(h.device, gen2, Vec::new(), None)
            .expect_err("one family's command went to another family's board");
        match error {
            SafetyError::Refused(Refusal::WrongFamily {
                device_family,
                command_family,
            }) => {
                assert_eq!(device_family, "royuan-yc500");
                assert_eq!(command_family, "royuan-gen2");
            }
            other => panic!("wrong refusal: {other:?}"),
        }
        assert_eq!(h.dispatched.get(), 0);
    }

    // --- the family has to be established before anything is written -------

    #[test]
    fn a_write_is_refused_until_the_family_is_verified() {
        // No command in the shipped ACL writes, so this exercises the decision
        // directly -- as it must, because the rule has to exist before the
        // first write ticket rather than after it.
        for confidence in [Confidence::Unknown, Confidence::Candidate, Confidence::High] {
            let mut h = harness(None);
            h.gate.identify_device(
                h.device,
                "royuan-gen2",
                FamilyConfidence::established(confidence),
            );
            h.gate.record_backup(h.device);
            assert_eq!(
                h.gate.authorize(
                    h.device,
                    "royuan-gen2",
                    "identify",
                    OpcodeClass::SlowFlash,
                    1_000,
                    None
                ),
                Err(Refusal::UnverifiedFamily {
                    confidence: FamilyConfidence::established(confidence)
                }),
                "a write went through at {confidence:?}"
            );
        }
    }

    #[test]
    fn a_read_is_allowed_below_verified() {
        // An unidentified-enough device opens read-only rather than not at all.
        let mut h = harness(None);
        h.gate.identify_device(
            h.device,
            "royuan-gen2",
            FamilyConfidence::established(Confidence::Candidate),
        );
        assert_eq!(
            h.gate.authorize(
                h.device,
                "royuan-gen2",
                "identify",
                OpcodeClass::SafeRead,
                1_000,
                None
            ),
            Ok(())
        );
        h.gate
            .execute(h.device, identify_command(), Vec::new(), None)
            .expect("a read on a merely-candidate device");
        assert_eq!(h.dispatched.get(), 1);
    }

    #[test]
    fn certainty_about_the_product_is_not_certainty_about_the_family() {
        // The gate is told one thing and one thing only: how well established
        // the *family* is. There is no argument it can make from anything else.
        let mut h = harness(None);
        h.gate.identify_device(
            h.device,
            "royuan-gen2",
            FamilyConfidence::established(Confidence::Unknown),
        );
        h.gate.record_backup(h.device);
        assert!(matches!(
            h.gate.authorize(
                h.device,
                "royuan-gen2",
                "identify",
                OpcodeClass::SafeWrite,
                1_000,
                None
            ),
            Err(Refusal::UnverifiedFamily { .. })
        ));
        assert_eq!(
            h.gate.family_confidence(h.device),
            FamilyConfidence::established(Confidence::Unknown)
        );
    }

    #[test]
    fn an_unverified_family_is_refused_before_the_backup_is_even_considered() {
        // Ordering matters for the message the user sees: "we do not know this
        // protocol" is the real reason, and "no backup" would be a distraction.
        let mut h = harness(None);
        h.gate.identify_device(
            h.device,
            "royuan-gen2",
            FamilyConfidence::established(Confidence::High),
        );
        assert!(matches!(
            h.gate.authorize(
                h.device,
                "royuan-gen2",
                "identify",
                OpcodeClass::SlowFlash,
                1_000,
                None
            ),
            Err(Refusal::UnverifiedFamily { .. })
        ));
    }

    #[test]
    fn a_write_without_a_backup_is_refused() {
        // No command in the shipped ACL writes, so this exercises the decision
        // directly. It is the rule that has to exist before the first write
        // ticket, not after it.
        let mut h = harness(None);
        h.gate.identify_device(
            h.device,
            "royuan-gen2",
            FamilyConfidence::established(Confidence::Verified),
        );
        assert_eq!(
            h.gate.authorize(
                h.device,
                "royuan-gen2",
                "identify",
                OpcodeClass::SlowFlash,
                1_000,
                None
            ),
            Err(Refusal::NoBackup)
        );

        h.gate.record_backup(h.device);
        assert_ne!(
            h.gate.authorize(
                h.device,
                "royuan-gen2",
                "identify",
                OpcodeClass::SlowFlash,
                1_000,
                None
            ),
            Err(Refusal::NoBackup),
            "a recorded backup should clear this particular refusal"
        );
    }

    #[test]
    fn a_read_needs_no_backup() {
        let mut h = harness(None);
        h.gate.identify_device(
            h.device,
            "royuan-gen2",
            FamilyConfidence::established(Confidence::Verified),
        );
        assert_eq!(
            h.gate.authorize(
                h.device,
                "royuan-gen2",
                "identify",
                OpcodeClass::SafeRead,
                1_000,
                None
            ),
            Ok(())
        );
    }

    // --- the happy path still has to work ---------------------------------

    #[test]
    fn an_approved_command_reaches_the_transport_with_its_own_opcode() {
        let mut h = harness(None);
        h.gate.identify_device(
            h.device,
            "royuan-gen2",
            FamilyConfidence::established(Confidence::Verified),
        );
        let answer = h
            .gate
            .execute(h.device, identify_command(), vec![0xAC, 0xAC], None)
            .expect("an approved read on an identified device");
        assert_eq!(h.dispatched.get(), 1);
        assert_eq!(
            h.seen_key.get(),
            Some(crate::key::CommandKey::Opcode(0x8F)),
            "the command identity must come from the table, never from the payload"
        );
        assert_eq!(answer, vec![0x8F]);
    }

    // --- the kill switch --------------------------------------------------

    #[test]
    fn a_stall_quarantines_the_device_and_nothing_is_retried() {
        let mut h = harness(Some(TransportError::EndpointStalled));
        h.gate.identify_device(
            h.device,
            "royuan-gen2",
            FamilyConfidence::established(Confidence::Verified),
        );

        let error = h
            .gate
            .execute(h.device, identify_command(), Vec::new(), None)
            .expect_err("the sink stalled");
        assert!(matches!(
            error,
            SafetyError::Transport(TransportError::EndpointStalled)
        ));
        assert_eq!(h.dispatched.get(), 1, "a stall must not be retried");
        assert!(h.gate.is_quarantined(h.device));
    }

    #[test]
    fn a_quarantined_device_refuses_reads_too() {
        // Reopening does not clear a stalled endpoint and further traffic keeps
        // it pinned, so "stop writing but keep polling" is not a safe halfway
        // position: everything stops.
        let mut h = harness(Some(TransportError::EndpointStalled));
        h.gate.identify_device(
            h.device,
            "royuan-gen2",
            FamilyConfidence::established(Confidence::Verified),
        );
        let _ = h
            .gate
            .execute(h.device, identify_command(), Vec::new(), None);

        h.now.set(10_000_000);
        let error = h
            .gate
            .execute(h.device, identify_command(), Vec::new(), None)
            .expect_err("a quarantined device stays quarantined");
        assert!(matches!(error, SafetyError::Refused(Refusal::Quarantined)));
        assert_eq!(h.dispatched.get(), 1, "waiting is not recovery");
    }

    #[test]
    fn only_reconnecting_clears_a_quarantine() {
        let mut h = harness(Some(TransportError::EndpointStalled));
        h.gate.identify_device(
            h.device,
            "royuan-gen2",
            FamilyConfidence::established(Confidence::Verified),
        );
        let _ = h
            .gate
            .execute(h.device, identify_command(), Vec::new(), None);
        assert!(h.gate.is_quarantined(h.device));

        h.gate.device_reconnected(h.device);
        assert!(!h.gate.is_quarantined(h.device));
        h.now.set(2_000);
        h.gate
            .execute(h.device, identify_command(), Vec::new(), None)
            .expect("after a physical reconnect the device is usable again");
        assert_eq!(h.dispatched.get(), 2);
    }

    #[test]
    fn an_ordinary_failure_does_not_quarantine() {
        // Only a typed stall trips the switch. A disconnected cable is not a
        // wedged endpoint, and treating every error as one would make the
        // product unusable while proving nothing.
        let mut h = harness(Some(TransportError::NotConnected(DeviceId::new(1))));
        h.gate.identify_device(
            h.device,
            "royuan-gen2",
            FamilyConfidence::established(Confidence::Verified),
        );
        let _ = h
            .gate
            .execute(h.device, identify_command(), Vec::new(), None);
        assert!(!h.gate.is_quarantined(h.device));
    }

    // --- cadence ----------------------------------------------------------

    #[test]
    fn a_rate_limited_command_never_reaches_the_transport() {
        let mut h = harness(None);
        h.gate.identify_device(
            h.device,
            "royuan-gen2",
            FamilyConfidence::established(Confidence::Verified),
        );
        h.gate
            .execute(h.device, identify_command(), Vec::new(), None)
            .expect("first");

        h.now.set(h.now.get() + 1);
        let error = h
            .gate
            .execute(h.device, identify_command(), Vec::new(), None)
            .expect_err("1 ms after the previous read, against a measured 12 ms");
        match error {
            SafetyError::Refused(Refusal::RateLimited { wait_ms }) => assert_eq!(wait_ms, 11),
            other => panic!("wrong refusal: {other:?}"),
        }
        assert_eq!(h.dispatched.get(), 1, "the refused command was still sent");
    }

    // --- the journal ------------------------------------------------------

    #[test]
    fn every_attempt_is_journalled_including_reads_and_refusals() {
        let mut h = harness(None);
        h.gate.identify_device(
            h.device,
            "royuan-gen2",
            FamilyConfidence::established(Confidence::Verified),
        );
        h.gate
            .execute(h.device, identify_command(), Vec::new(), None)
            .expect("first");
        h.now.set(h.now.get() + 1);
        let _ = h
            .gate
            .execute(h.device, identify_command(), Vec::new(), None);

        let entries = h.entries.borrow();
        assert_eq!(entries.len(), 2, "a refusal is as journalled as a success");
        assert_eq!(entries[0].outcome, Outcome::Completed);
        assert_eq!(entries[0].intent, Intent::Probe);
        assert_eq!(entries[0].command, "identify");
        assert_eq!(entries[0].family, "royuan-gen2");
        assert!(matches!(
            entries[1].outcome,
            Outcome::Refused(Refusal::RateLimited { .. })
        ));
    }

    #[test]
    fn the_journal_records_the_payload_length_and_nothing_of_its_contents() {
        let mut h = harness(None);
        h.gate.identify_device(
            h.device,
            "royuan-gen2",
            FamilyConfidence::established(Confidence::Verified),
        );
        let secret = vec![0x53, 0x45, 0x43, 0x52, 0x45, 0x54];
        h.gate
            .execute(h.device, identify_command(), secret, None)
            .expect("dispatched");

        let entries = h.entries.borrow();
        assert_eq!(entries[0].payload_len, 6);
        let rendered = format!("{:?}", entries[0]);
        assert!(
            !rendered.contains("83") && !rendered.contains("69") && !rendered.contains("SECRET"),
            "payload material reached the journal: {rendered}"
        );
    }

    #[test]
    fn a_failed_attempt_is_journalled_by_failure_kind() {
        let mut h = harness(Some(TransportError::Backend("Ошибка протокола".to_owned())));
        h.gate.identify_device(
            h.device,
            "royuan-gen2",
            FamilyConfidence::established(Confidence::Verified),
        );
        let _ = h
            .gate
            .execute(h.device, identify_command(), Vec::new(), None);

        let entries = h.entries.borrow();
        assert_eq!(entries[0].outcome, Outcome::Failed(FailureKind::Backend));
        let rendered = format!("{:?}", entries[0]);
        assert!(
            !rendered.contains("Ошибка"),
            "the backend's localised text must not be carried around: {rendered}"
        );
    }

    #[test]
    fn a_read_is_journalled_as_needing_no_verification() {
        let mut h = harness(None);
        h.gate.identify_device(
            h.device,
            "royuan-gen2",
            FamilyConfidence::established(Confidence::Verified),
        );
        h.gate
            .execute(h.device, identify_command(), Vec::new(), None)
            .expect("dispatched");
        assert_eq!(
            h.entries.borrow()[0].verification,
            Verification::NotApplicable
        );
    }
}
