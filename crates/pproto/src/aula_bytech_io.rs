//! The one adapter between the `aula-bytech` codec and a HID channel.
//!
//! This is the narrow place where a command becomes bytes and bytes become a
//! response, for this family. Nothing else in the workspace encodes an
//! `aula-bytech` frame or writes one, and the reason is the finding recorded on
//! TICKET-15: `ptransport` sits at the bottom of the dependency graph, cannot
//! depend on `psafety`, and therefore cannot demand a type only `psafety` can
//! mint. Keeping every raw write for this family in one module is what makes
//! "who may call the raw transport" a question with an answer.
//!
//! # What it does
//!
//! One exchange: encode, write once, read until a deadline, discard unsolicited
//! event reports, hand back the first report that could be an answer. Deciding
//! whether it *is* the answer belongs to the decoder, which checks the echoed
//! header; this layer only refuses to mistake an event for a reply.
//!
//! # What it does not do
//!
//! No retry, no second write, no fallback command, and no opinion about which
//! command it is carrying. It reads the key off whatever authorised value it is
//! handed, so it cannot address a command the gate did not approve.

use std::time::{Duration, Instant};

use psafety::probe::{AuthorizedProbe, ProbeSink};
use psafety::{AuthorizedCommand, CommandSink};
use ptransport::{DeviceId, ProbeChannel, ReceivedReport, TransportError};

use crate::aula_bytech;

/// Everything that crossed the wire during one exchange.
///
/// Kept so a report can quote the exchange rather than paraphrase it, and so a
/// report that was ignored is still visible: "we saw three reports and used the
/// second" is a materially different account from "we got an answer".
#[derive(Clone, Default, Debug)]
pub struct Transcript {
    /// The bytes handed to the backend, report id included.
    pub sent: Vec<u8>,
    /// Every report seen inside the window, in arrival order, with the verdict
    /// this layer reached about each.
    pub seen: Vec<(ReceivedReport, Verdict)>,
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Verdict {
    /// An event this protocol sends unprompted. Never an answer.
    UnsolicitedEvent,
    /// Could be the answer; the decoder decides.
    CandidateAnswer,
}

/// One `aula-bytech` exchange over one open channel.
///
/// Borrows the channel rather than owning it, because the channel's lifetime is
/// the caller's business: a probe closes it afterwards, a session keeps it.
pub struct Exchange<'a> {
    channel: &'a mut ProbeChannel,
    timeout: Duration,
    transcript: Transcript,
}

impl<'a> Exchange<'a> {
    pub fn new(channel: &'a mut ProbeChannel, timeout: Duration) -> Self {
        Self {
            channel,
            timeout,
            transcript: Transcript::default(),
        }
    }

    pub fn transcript(&self) -> &Transcript {
        &self.transcript
    }

    /// Encodes, writes once, and returns the first report that is not an event.
    ///
    /// Private because the two public entry points are the two sink
    /// implementations: a caller reaches this by holding something a gate
    /// minted, never by assembling a key of its own.
    ///
    /// Both sinks exist because both doors are in use. `read_model_id` came
    /// through the probe door and now goes through the production one; the two
    /// actuation reads are at the probe door today. The adapter does not care
    /// which -- it encodes whatever authorised value it is handed -- and that is
    /// the point of it being one adapter.
    fn run(&mut self, key: psafety::CommandKey, payload: &[u8]) -> Result<Vec<u8>, TransportError> {
        let report_id = self.channel.report_id();
        let frame = aula_bytech::encode_request(key, payload, report_id)
            .map_err(|error| TransportError::Backend(error.to_string()))?;

        self.transcript.sent = self.channel.write_report(&frame)?;

        let deadline = Instant::now() + self.timeout;
        loop {
            let now = Instant::now();
            if now >= deadline {
                return Err(TransportError::Backend(
                    "no candidate answer within the window".into(),
                ));
            }
            let Some(report) = self.channel.read_report(deadline - now)? else {
                return Err(TransportError::Backend(
                    "no candidate answer within the window".into(),
                ));
            };

            // The report id is byte zero on a numbered collection. Strip it so
            // the frame offsets mean what the codec says they mean.
            let body: Vec<u8> = match report.bytes.split_first() {
                Some((first, rest)) if *first == report_id => rest.to_vec(),
                _ => report.bytes.clone(),
            };

            if aula_bytech::is_unsolicited(&body) {
                self.transcript
                    .seen
                    .push((report, Verdict::UnsolicitedEvent));
                continue;
            }
            self.transcript
                .seen
                .push((report, Verdict::CandidateAnswer));
            return Ok(body);
        }
    }
}

impl ProbeSink for Exchange<'_> {
    fn dispatch_probe(
        &mut self,
        _device: DeviceId,
        probe: AuthorizedProbe,
    ) -> Result<Vec<u8>, TransportError> {
        self.run(probe.key(), probe.payload())
    }
}

impl CommandSink for Exchange<'_> {
    fn dispatch(
        &mut self,
        _device: DeviceId,
        command: AuthorizedCommand,
    ) -> Result<Vec<u8>, TransportError> {
        self.run(command.key(), command.payload())
    }
}
