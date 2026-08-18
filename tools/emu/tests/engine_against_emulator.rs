//! The production read path, pointed at a device that does not exist.
//!
//! This is the test TICKET-14 is actually for. Everything below `pemu` runs
//! unmodified: `AulaBytechEngine`, `SafetyGate`, `SafeCommandId`, the
//! `aula-bytech` codec and `Exchange`. The only substitution is at the very
//! bottom, where an [`EmulatedChannel`] stands in for a `ProbeChannel`, and the
//! layers above cannot tell.
//!
//! If any of these tests needed a `#[cfg(test)]` branch, a mock hook or a
//! special constructor inside a production crate, the transport abstraction
//! would be leaking and that would be the finding rather than the workaround.
//! None of them do.
//!
//! Nothing here touches hardware. It is the whole point: this runs on a CI
//! machine with no keyboard attached to it.

#![allow(clippy::expect_used, clippy::panic, clippy::unwrap_used)]

use std::time::Duration;

use pcaps::{Confidence, FamilyConfidence};
use pemu::{Aula84He, EmulatedChannel, FakeFirmware};
use pproto::aula_bytech_engine::AulaBytechEngine;
use pproto::aula_bytech_he::{KeyId, TravelScale, WASD_KEY_IDS, WasdTravelProbe};
use pproto::aula_bytech_io::{Exchange, Verdict};
use psafety::journal::{JournalEntry, JournalSink, Outcome, Refusal};
use psafety::probe::{ProbeError, ProbeGate};
use psafety::rate::UserConfirmation;
use psafety::{Clock, ReadError, SafetyGate};
use ptransport::DeviceId;

const TIMEOUT: Duration = Duration::from_millis(50);

fn device() -> DeviceId {
    DeviceId::new(0x372E_103E)
}

/// A clock a test controls, so cadence is about cadence and not about sleeping.
#[derive(Default)]
struct TestClock {
    now: std::cell::Cell<u64>,
}

impl Clock for TestClock {
    fn now_ms(&self) -> u64 {
        self.now.get()
    }
}

#[derive(Default)]
struct CollectingJournal {
    entries: std::rc::Rc<std::cell::RefCell<Vec<JournalEntry>>>,
}

impl JournalSink for CollectingJournal {
    fn record(&mut self, entry: JournalEntry) {
        self.entries.borrow_mut().push(entry);
    }
}

fn verified() -> FamilyConfidence {
    FamilyConfidence::established(Confidence::Verified)
}

// --- the production read path -----------------------------------------------

#[test]
fn the_engine_reads_a_model_id_from_a_device_that_does_not_exist() {
    let mut channel = EmulatedChannel::new(Aula84He::observed());
    let mut exchange = Exchange::new(&mut channel, TIMEOUT);
    let mut gate = SafetyGate::new(
        &mut exchange,
        CollectingJournal::default(),
        TestClock::default(),
    );
    gate.identify_device(device(), AulaBytechEngine::family(), verified());

    let model_id = AulaBytechEngine::read_model_id(&mut gate, device(), None)
        .expect("the recorded answer decodes");

    // The value exchange 001 recorded from the physical board.
    assert_eq!(model_id.raw(), 18_691_697_672_197);
}

#[test]
fn the_engine_never_learns_which_report_id_it_is_talking_on() {
    // The emulator numbers its collection 9 because our board's descriptor does.
    // The engine is handed no report id anywhere, and the frame that goes out
    // carries one, so the number came from the channel.
    let mut channel = EmulatedChannel::new(Aula84He::observed());
    {
        let mut exchange = Exchange::new(&mut channel, TIMEOUT);
        let mut gate = SafetyGate::new(
            &mut exchange,
            CollectingJournal::default(),
            TestClock::default(),
        );
        gate.identify_device(device(), AulaBytechEngine::family(), verified());
        AulaBytechEngine::read_model_id(&mut gate, device(), None).expect("a read");
        assert_eq!(exchange.transcript().sent.first(), Some(&9));
    }
    let endpoint = AulaBytechEngine::config_endpoint();
    assert_eq!(
        (endpoint.usage_page, endpoint.usage),
        (pemu::aula::CONFIG_USAGE_PAGE, pemu::aula::CONFIG_USAGE),
        "the engine and the fixture agree on the endpoint, and on nothing below it"
    );
}

#[test]
fn a_command_for_another_family_never_reaches_the_device() {
    let mut channel = EmulatedChannel::new(Aula84He::observed());
    let mut exchange = Exchange::new(&mut channel, TIMEOUT);
    let mut gate = SafetyGate::new(
        &mut exchange,
        CollectingJournal::default(),
        TestClock::default(),
    );
    // The board is identified as something else entirely.
    gate.identify_device(device(), "royuan-gen2", verified());

    let error = AulaBytechEngine::read_model_id(&mut gate, device(), None)
        .expect_err("an aula command must not go to a ROYUAN board");
    assert!(matches!(
        error,
        ReadError::Refused(Refusal::WrongFamily { .. })
    ));
    drop(gate);
    assert!(
        channel.log().is_empty(),
        "the gate refused and the device still saw a write"
    );
}

#[test]
fn an_unidentified_device_is_refused_before_anything_is_written() {
    let mut channel = EmulatedChannel::new(Aula84He::observed());
    let mut exchange = Exchange::new(&mut channel, TIMEOUT);
    let mut gate = SafetyGate::new(
        &mut exchange,
        CollectingJournal::default(),
        TestClock::default(),
    );
    // identify_device is deliberately not called.
    let error = AulaBytechEngine::read_model_id(&mut gate, device(), None)
        .expect_err("nothing identified this board");
    assert!(matches!(
        error,
        ReadError::Refused(Refusal::DeviceNotIdentified)
    ));
    drop(gate);
    assert!(channel.log().is_empty());
}

#[test]
fn the_measured_cadence_is_enforced_against_a_fake_device_too() {
    // read_model_id declares 1000 ms in the ACL. The second read inside that
    // window is refused, and the refusal happens above the transport, so the
    // fake device sees exactly one write.
    let mut channel = EmulatedChannel::new(Aula84He::observed());
    let mut exchange = Exchange::new(&mut channel, TIMEOUT);
    let clock = TestClock::default();
    let mut gate = SafetyGate::new(&mut exchange, CollectingJournal::default(), clock);
    gate.identify_device(device(), AulaBytechEngine::family(), verified());

    AulaBytechEngine::read_model_id(&mut gate, device(), None).expect("the first read");
    let error = AulaBytechEngine::read_model_id(&mut gate, device(), None)
        .expect_err("the second read is too soon");
    assert!(matches!(
        error,
        ReadError::Refused(Refusal::RateLimited { .. })
    ));
    drop(gate);
    assert_eq!(channel.log().len(), 1, "one write, not two");
}

// --- the lies ---------------------------------------------------------------

#[test]
fn an_unsupported_command_replaying_the_previous_answer_is_refused() {
    // The anti-fiction case, end to end, and the reason the fixture has to lie
    // before anything can be shown refusing a lie.
    //
    // 0x95 is the per-key switch-type read. It was recovered from the vendor
    // artifact, has never been sent to any board, and this fixture has no answer
    // recorded for it -- so it does what real firmware in this class does and
    // sends the previous answer again. A decoder that treated "bytes came back"
    // as success would report a model id as an actuation reading.
    let mut firmware = Aula84He::observed();
    let previous = firmware
        .answer(&frame(&[0x82, 0x01, 0x00, 0x01, 0x00, 0x06]), None)
        .bytes()
        .expect("an answer")
        .to_vec();

    let replayed = firmware.answer(
        &frame(&[0x95, 0x00, 0x00, 0x01, 0x00, 0x08]),
        Some(&previous),
    );
    assert_eq!(
        replayed.bytes(),
        Some(previous.as_slice()),
        "an unsupported command must replay, or this test proves nothing"
    );

    // Now the real decoder, handed exactly those bytes as if they answered the
    // actuation read.
    use psafety::probe::ProbeResponse;
    let rejection = WasdTravelProbe::decode(key_travel_command(), &previous)
        .expect_err("a replayed model id is not an actuation reading");
    assert!(
        format!("{rejection}").contains("frame"),
        "the refusal should come from the echoed header: {rejection}"
    );
}

#[test]
fn silence_is_reported_as_silence_and_not_as_a_value() {
    let mut firmware = Aula84He::observed();
    firmware.go_silent_on_next_write();
    let mut channel = EmulatedChannel::new(firmware);
    let mut exchange = Exchange::new(&mut channel, TIMEOUT);
    let mut gate = SafetyGate::new(
        &mut exchange,
        CollectingJournal::default(),
        TestClock::default(),
    );
    gate.identify_device(device(), AulaBytechEngine::family(), verified());

    let error = AulaBytechEngine::read_model_id(&mut gate, device(), None)
        .expect_err("the device said nothing");
    assert!(
        matches!(error, ReadError::Transport(_)),
        "silence is a transport outcome, not a decoded value: {error:?}"
    );
}

#[test]
fn a_stall_quarantines_the_device_and_only_a_reconnect_clears_it() {
    let mut firmware = Aula84He::observed();
    firmware.stall_on_next_write();
    let mut channel = EmulatedChannel::new(firmware);
    let mut exchange = Exchange::new(&mut channel, TIMEOUT);
    let mut gate = SafetyGate::new(
        &mut exchange,
        CollectingJournal::default(),
        TestClock::default(),
    );
    gate.identify_device(device(), AulaBytechEngine::family(), verified());

    let error = AulaBytechEngine::read_model_id(&mut gate, device(), None)
        .expect_err("the endpoint stalled");
    assert!(matches!(error, ReadError::Transport(_)));
    assert!(
        gate.is_quarantined(device()),
        "a typed stall must trip the kill switch"
    );

    // Quarantine stops reads too, not only writes.
    let refused = AulaBytechEngine::read_model_id(&mut gate, device(), None)
        .expect_err("a quarantined device refuses everything");
    assert!(matches!(refused, ReadError::Refused(Refusal::Quarantined)));

    gate.device_reconnected(device());
    assert!(!gate.is_quarantined(device()));
}

#[test]
fn unsolicited_event_reports_are_skipped_rather_than_decoded() {
    // 0x98 is live key travel and arrives unprompted. The codec routes it as an
    // event; if it did not, the first thing the reader saw after a write would
    // be an event frame and every read would fail or, worse, decode.
    let mut event = vec![0u8; 64];
    event[0] = 9;
    event[1] = 0x98;
    let mut channel =
        EmulatedChannel::new(Aula84He::observed()).with_unsolicited(vec![event.clone(), event]);
    let mut exchange = Exchange::new(&mut channel, TIMEOUT);
    let mut gate = SafetyGate::new(
        &mut exchange,
        CollectingJournal::default(),
        TestClock::default(),
    );
    gate.identify_device(device(), AulaBytechEngine::family(), verified());

    let model_id = AulaBytechEngine::read_model_id(&mut gate, device(), None)
        .expect("the answer, not an event");
    assert_eq!(model_id.raw(), 18_691_697_672_197);
    drop(gate);

    let seen = &exchange.transcript().seen;
    assert_eq!(seen.len(), 3, "two events and one answer");
    assert_eq!(seen[0].1, Verdict::UnsolicitedEvent);
    assert_eq!(seen[1].1, Verdict::UnsolicitedEvent);
    assert_eq!(seen[2].1, Verdict::CandidateAnswer);
}

// --- the actuation probe, against the fixture -------------------------------

#[test]
fn the_actuation_probe_decodes_the_recorded_wasd_answer() {
    let mut channel = EmulatedChannel::new(Aula84He::observed());
    let mut exchange = Exchange::new(&mut channel, TIMEOUT);
    let gate = ProbeGate::new(
        &mut exchange,
        CollectingJournal::default(),
        TestClock::default(),
    );

    let probe = gate
        .probe::<WasdTravelProbe>(
            device(),
            AulaBytechEngine::family(),
            verified(),
            UserConfirmation::given(),
        )
        .expect("the recorded answer decodes");

    assert_eq!(probe.travels.len(), 4);
    for (travel, expected) in probe.travels.iter().zip(WASD_KEY_IDS) {
        assert_eq!(travel.key_id, expected);
        assert_eq!(travel.raw, 40, "what the board reported on 2026-08-18");
        assert_eq!(travel.to_millimetres(TravelScale::VendorFallback), 0.40);
    }
}

#[test]
fn a_reordered_answer_is_refused_rather_than_misattributed() {
    // The fixture answers in the order asked, so this drives the decoder
    // directly with an answer the fixture would produce for a reversed request.
    // The failure being prevented is silent: one key's setting reported as
    // another's, with nothing about the frame looking wrong.
    let mut firmware = Aula84He::observed();
    let reversed = firmware
        .answer(
            &frame(&[
                0x93, 0x00, 0x00, 0x01, 0x00, 0x08, 0x00, 45, 0x00, 44, 0x00, 43, 0x00, 30,
            ]),
            None,
        )
        .bytes()
        .expect("an answer")
        .to_vec();

    use psafety::probe::ProbeResponse;
    let rejection = WasdTravelProbe::decode(key_travel_command(), &reversed)
        .expect_err("D's value must not be reported as W's");
    let text = format!("{rejection}");
    assert!(
        text.contains("key 45") && text.contains("key 30"),
        "the error should name both keys: {text}"
    );
}

#[test]
fn the_wrong_key_id_space_produces_a_well_formed_answer_about_the_wrong_keys() {
    // The defect that shipped, reproducible without hardware from now on.
    //
    // A request built from HID usages decodes cleanly -- valid frame, four
    // records, right order -- and reports the actuation of `=`, F3, `8` and F6.
    // Nothing in the response is malformed, which is exactly why this took a
    // second hardware exchange to notice, and exactly why it is worth a test.
    let mut firmware = Aula84He::observed();
    let hid_usage_request = firmware
        .answer(
            &frame(&[
                0x93, 0x00, 0x00, 0x01, 0x00, 0x08, 0x00, 0x1A, 0x00, 0x04, 0x00, 0x16, 0x00, 0x07,
            ]),
            None,
        )
        .bytes()
        .expect("a perfectly well-formed answer")
        .to_vec();

    // Twenty data bytes, four records: structurally indistinguishable from the
    // right answer.
    assert_eq!(hid_usage_request[5], 0x14);
    let ids: Vec<u16> = hid_usage_request[6..26]
        .chunks_exact(5)
        .map(|r| u16::from_be_bytes([r[0], r[1]]))
        .collect();
    assert_eq!(ids, vec![0x1A, 0x04, 0x16, 0x07]);
    let travels: Vec<u16> = hid_usage_request[6..26]
        .chunks_exact(5)
        .map(|r| u16::from_be_bytes([r[2], r[3]]))
        .collect();
    assert_eq!(travels, vec![79, 79, 79, 79], "four keys nobody configured");

    // And the request our code actually builds names the other four.
    assert_ne!(ids[0], KeyId::W.raw());
    assert_eq!(WASD_KEY_IDS.map(KeyId::raw), [30, 43, 44, 45]);
}

// --- helpers ----------------------------------------------------------------

/// The command key for `read_key_travel`, for driving the decoder directly.
fn key_travel_command() -> psafety::CommandKey {
    psafety::CommandKey::GroupSubcommand {
        group: 0x93,
        subcommand: 0x00,
    }
}

/// A 63-byte frame with `prefix` at the front, the way the codec builds one.
fn frame(prefix: &[u8]) -> Vec<u8> {
    let mut out = vec![0u8; pemu::aula::FRAME_LEN];
    out[..prefix.len()].copy_from_slice(prefix);
    out
}

/// Journal entries are recorded for reads against a fake device exactly as they
/// are against a real one.
#[test]
fn every_exchange_is_journalled() {
    let entries = std::rc::Rc::new(std::cell::RefCell::new(Vec::new()));
    let journal = CollectingJournal {
        entries: std::rc::Rc::clone(&entries),
    };
    let mut channel = EmulatedChannel::new(Aula84He::observed());
    let mut exchange = Exchange::new(&mut channel, TIMEOUT);
    let mut gate = SafetyGate::new(&mut exchange, journal, TestClock::default());
    gate.identify_device(device(), AulaBytechEngine::family(), verified());
    AulaBytechEngine::read_model_id(&mut gate, device(), None).expect("a read");

    let entries = entries.borrow();
    assert_eq!(entries.len(), 1);
    assert_eq!(entries[0].command, "read_model_id");
    assert_eq!(entries[0].outcome, Outcome::Completed);
    assert_eq!(entries[0].payload_len, 6);
}

/// The probe path refuses a probe whose family does not match, against a fake
/// device just as against a real one.
#[test]
fn a_probe_for_the_wrong_family_never_reaches_the_fake_device() {
    let mut channel = EmulatedChannel::new(Aula84He::observed());
    let mut exchange = Exchange::new(&mut channel, TIMEOUT);
    let gate = ProbeGate::new(
        &mut exchange,
        CollectingJournal::default(),
        TestClock::default(),
    );
    let error = gate
        .probe::<WasdTravelProbe>(
            device(),
            "royuan-gen2",
            verified(),
            UserConfirmation::given(),
        )
        .expect_err("wrong family");
    assert!(matches!(
        error,
        ProbeError::Refused(Refusal::WrongFamily { .. })
    ));
    drop(exchange);
    assert!(channel.log().is_empty());
}
