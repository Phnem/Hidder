//! A fake HID device that lies the way real firmware lies.
//!
//! # Why an emulator that only answers correctly would be useless
//!
//! The point of this crate is not to have something to talk to when no board is
//! plugged in. It is to reproduce the *specific* ways real firmware misleads a
//! host, because those are the paths that decide whether this project ships a
//! confidently wrong number:
//!
//! - **An unsupported command replays the previous answer** instead of
//!   refusing. Documented behaviour on boards in this class, and the reason
//!   `ProbeResponse::decode` exists at all. An emulator that returned an error
//!   for an unknown command would let a decoder that never checks the echoed
//!   header pass every test it has.
//! - **A well-formed answer about the wrong thing.** Exchange 004, on this
//!   project's own board: a request naming four valid key ids from the wrong
//!   number space came back valid in every respect, in the right order, with a
//!   matching checksum, describing four keys nobody had asked about.
//!   [`Aula84He`] answers per key id from a recorded table, so a request built
//!   from the wrong space gets exactly that treatment here too.
//! - **Silence**, which is not evidence of a missing capability, and which the
//!   code above has to survive rather than retry through.
//! - **A stall**, which does not clear by reopening and which more traffic keeps
//!   pinned.
//!
//! # What is recorded and what is computed
//!
//! Recorded, byte for byte, from this project's own exchanges with its own
//! board -- `docs/hardware/aula-bytech-exchange-00{1,3,4}-*.md`. Nothing here
//! recomputes a checksum or re-encodes a frame. A fixture whose answers came
//! out of our encoder would agree with our encoder by construction and could
//! never contradict it, and contradicting it is the entire job.
//!
//! # What this is not
//!
//! Not a HID stack. It implements [`ReportChannel`] -- the contract the layers
//! above are written against -- and does not pretend to be an operating system,
//! enumerate itself, or own a device node. The claim it exists to support is
//! narrow and worth stating exactly: **nothing above `ptransport` knows whether
//! a device is real.**

use std::cell::RefCell;
use std::collections::VecDeque;
use std::time::Duration;

use ptransport::{ReceivedReport, ReportChannel, TransportError};

pub mod aula;
pub mod topology;

pub use aula::Aula84He;
pub use topology::{Collection, Topology};

/// How a fake device decided to answer one request.
#[derive(Clone, PartialEq, Eq, Debug)]
pub enum Answer {
    /// A recorded answer to a request this device knows.
    Recorded(Vec<u8>),
    /// The previous answer, sent again because this request is not one the
    /// device supports. The lie worth reproducing.
    ReplayedPrevious(Vec<u8>),
    /// The request, returned unchanged. What our board did to
    /// `read_travel_precision`, and indistinguishable from a genuine "nothing
    /// to report" (exchange 003).
    EchoedRequest(Vec<u8>),
    /// Nothing at all, until the caller's deadline passes.
    Silence,
    /// The endpoint wedges, and stays wedged until it is reconnected.
    Stall,
}

impl Answer {
    /// The bytes that go on the wire, if any.
    pub fn bytes(&self) -> Option<&[u8]> {
        match self {
            Answer::Recorded(bytes)
            | Answer::ReplayedPrevious(bytes)
            | Answer::EchoedRequest(bytes) => Some(bytes),
            Answer::Silence | Answer::Stall => None,
        }
    }
}

/// What a fake device does with the bytes it is written.
///
/// One request per call, deliberately: a device that could be asked several
/// questions at once would not be modelling anything this project's transport
/// is able to do.
pub trait FakeFirmware {
    /// The report id this device numbers its config collection with.
    fn report_id(&self) -> u8;

    /// Answers one written report. `written` excludes the report id byte.
    ///
    /// `previous` is the last answer this device gave, so an implementation can
    /// reproduce the replay lie without keeping a second copy of it.
    fn answer(&mut self, written: &[u8], previous: Option<&[u8]>) -> Answer;
}

/// One request and what came back, for a test to assert on.
#[derive(Clone, PartialEq, Eq, Debug)]
pub struct LoggedExchange {
    /// Bytes handed to the channel, report id included.
    pub written: Vec<u8>,
    pub answer: Answer,
}

/// A channel to a fake device, satisfying the same contract as a real one.
///
/// Queues its replies rather than answering inline, because a board that sends
/// unsolicited event reports is a thing this family actually does -- live key
/// travel arrives that way -- and the codec above is supposed to skip them. It
/// can only be shown to skip them against something that emits them.
pub struct EmulatedChannel<F: FakeFirmware> {
    firmware: F,
    /// Reports waiting to be read. Behind a `RefCell` because [`ReportChannel`]
    /// reads through `&self`, which is the real channel's signature: there, the
    /// mutation happens inside the OS. Matching the signature exactly is the
    /// point of this type, so the interior mutability lives here instead.
    pending: RefCell<VecDeque<Vec<u8>>>,
    previous: Option<Vec<u8>>,
    interleave: Vec<Vec<u8>>,
    stalled: bool,
    log: Vec<LoggedExchange>,
}

impl<F: FakeFirmware> EmulatedChannel<F> {
    pub fn new(firmware: F) -> Self {
        Self {
            firmware,
            pending: RefCell::new(VecDeque::new()),
            previous: None,
            interleave: Vec::new(),
            stalled: false,
            log: Vec::new(),
        }
    }

    /// Emits these reports ahead of the next answer, the way unsolicited events
    /// arrive.
    ///
    /// Each is a whole report including its report id, because that is what one
    /// looks like on the wire.
    pub fn with_unsolicited(mut self, reports: Vec<Vec<u8>>) -> Self {
        self.interleave = reports;
        self
    }

    /// Every exchange this channel has served, in order.
    pub fn log(&self) -> &[LoggedExchange] {
        &self.log
    }

    /// Whether this device is wedged. Cleared only by
    /// [`EmulatedChannel::reconnect`].
    pub fn is_stalled(&self) -> bool {
        self.stalled
    }

    /// The physical act, modelled: unplugged and plugged back in.
    ///
    /// Named after the act rather than after a timeout because that is the
    /// requirement -- a stalled endpoint does not recover from waiting.
    pub fn reconnect(&mut self) {
        self.stalled = false;
        self.pending.borrow_mut().clear();
        self.previous = None;
    }

    pub fn firmware(&self) -> &F {
        &self.firmware
    }
}

impl<F: FakeFirmware> ReportChannel for EmulatedChannel<F> {
    fn report_id(&self) -> u8 {
        self.firmware.report_id()
    }

    fn write_report(&mut self, frame: &[u8]) -> Result<Vec<u8>, TransportError> {
        let report_id = self.firmware.report_id();
        let mut written = Vec::with_capacity(frame.len() + 1);
        written.push(report_id);
        written.extend_from_slice(frame);

        if self.stalled {
            // Writing to a wedged endpoint keeps it wedged. Modelled rather
            // than merely described, because a kill switch is only worth having
            // if something can trip it in a test.
            return Err(TransportError::EndpointStalled);
        }

        let answer = self.firmware.answer(frame, self.previous.as_deref());
        self.log.push(LoggedExchange {
            written: written.clone(),
            answer: answer.clone(),
        });

        match &answer {
            Answer::Stall => {
                self.stalled = true;
                return Err(TransportError::EndpointStalled);
            }
            Answer::Silence => {}
            _ => {
                let mut pending = self.pending.borrow_mut();
                for event in self.interleave.drain(..) {
                    pending.push_back(event);
                }
                if let Some(bytes) = answer.bytes() {
                    self.previous = Some(bytes.to_vec());
                    let mut report = Vec::with_capacity(bytes.len() + 1);
                    report.push(report_id);
                    report.extend_from_slice(bytes);
                    pending.push_back(report);
                }
            }
        }
        Ok(written)
    }

    fn read_report(&self, _timeout: Duration) -> Result<Option<ReceivedReport>, TransportError> {
        if self.stalled {
            return Err(TransportError::EndpointStalled);
        }
        // Nothing to wait for: a fake device has already decided. Sleeping to
        // simulate latency would make every test slower and not one of them
        // stronger, so the timeout is accepted and ignored.
        Ok(self
            .pending
            .borrow_mut()
            .pop_front()
            .map(|bytes| ReceivedReport {
                after: Duration::from_millis(1),
                bytes,
            }))
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::expect_used, clippy::panic, clippy::unwrap_used)]

    use super::*;

    /// A device with one command and no memory of anything else.
    struct OneCommand {
        answered: usize,
    }

    impl FakeFirmware for OneCommand {
        fn report_id(&self) -> u8 {
            9
        }

        fn answer(&mut self, written: &[u8], previous: Option<&[u8]>) -> Answer {
            self.answered += 1;
            match written.first() {
                Some(0x82) => Answer::Recorded(vec![0x82, 0x01, 0xAA]),
                _ => match previous {
                    Some(bytes) => Answer::ReplayedPrevious(bytes.to_vec()),
                    None => Answer::Silence,
                },
            }
        }
    }

    fn channel() -> EmulatedChannel<OneCommand> {
        EmulatedChannel::new(OneCommand { answered: 0 })
    }

    fn read(channel: &EmulatedChannel<OneCommand>) -> Option<Vec<u8>> {
        channel
            .read_report(Duration::from_millis(1))
            .expect("no stall")
            .map(|report| report.bytes)
    }

    #[test]
    fn a_known_command_gets_its_recorded_answer_with_the_report_id_in_front() {
        let mut channel = channel();
        let written = channel.write_report(&[0x82, 0x01]).expect("write");
        assert_eq!(written[0], 9, "the report id goes out in front");
        assert_eq!(read(&channel), Some(vec![9, 0x82, 0x01, 0xAA]));
    }

    #[test]
    fn an_unknown_command_replays_the_previous_answer() {
        // The lie this whole crate exists for. A decoder that trusts "bytes came
        // back" reads this as a successful exchange with a command the device
        // has never heard of.
        let mut channel = channel();
        channel.write_report(&[0x82, 0x01]).expect("write");
        let first = read(&channel).expect("an answer");
        channel.write_report(&[0x77, 0x99]).expect("write");
        let second = read(&channel).expect("an answer, wrongly");
        assert_eq!(first, second, "the replay is what makes it a lie");
        assert!(matches!(
            channel.log()[1].answer,
            Answer::ReplayedPrevious(_)
        ));
    }

    #[test]
    fn the_first_unknown_command_has_nothing_to_replay_and_says_nothing() {
        let mut channel = channel();
        channel.write_report(&[0x77]).expect("write");
        assert_eq!(read(&channel), None, "silence, not an invented answer");
    }

    #[test]
    fn unsolicited_reports_arrive_before_the_answer() {
        let mut channel =
            channel().with_unsolicited(vec![vec![9, 0x98, 0x00], vec![9, 0x94, 0x00]]);
        channel.write_report(&[0x82, 0x01]).expect("write");
        assert_eq!(read(&channel), Some(vec![9, 0x98, 0x00]));
        assert_eq!(read(&channel), Some(vec![9, 0x94, 0x00]));
        assert_eq!(read(&channel), Some(vec![9, 0x82, 0x01, 0xAA]));
    }

    #[test]
    fn a_stall_does_not_clear_by_writing_again() {
        struct Wedged;
        impl FakeFirmware for Wedged {
            fn report_id(&self) -> u8 {
                9
            }
            fn answer(&mut self, _written: &[u8], _previous: Option<&[u8]>) -> Answer {
                Answer::Stall
            }
        }
        let mut channel = EmulatedChannel::new(Wedged);
        assert!(channel.write_report(&[0x82]).is_err());
        assert!(channel.is_stalled());
        assert!(
            channel.write_report(&[0x82]).is_err(),
            "traffic keeps a stalled endpoint pinned"
        );
        assert!(channel.read_report(Duration::from_millis(1)).is_err());
        channel.reconnect();
        assert!(!channel.is_stalled(), "only reconnecting clears it");
    }
}
