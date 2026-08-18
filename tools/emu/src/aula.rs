//! The AULA HERO 84 HE, as this project has actually observed it.
//!
//! Every byte below was recorded from the physical board and is reproduced
//! rather than recomputed. The distinction is the whole value of the fixture: a
//! response synthesised by our own encoder would agree with our own decoder no
//! matter what either of them believed, and could never catch the class of
//! defect that exchange 004 caught on hardware.
//!
//! Sources, all in `docs/hardware/`:
//!
//! | What | Exchange |
//! |---|---|
//! | `read_model_id` request and answer | `aula-bytech-exchange-001.md` |
//! | `read_travel_precision` echo | `aula-bytech-exchange-003-actuation.md` |
//! | `read_key_travel` for `=` F3 `8` F6 | `aula-bytech-exchange-003-actuation.md` |
//! | `read_key_travel` for W A S D | `aula-bytech-exchange-004-actuation.md` |
//!
//! The topology -- collections, usages, report sizes -- comes from
//! `aula-hero-84-he.json`, the TICKET-08 capture, and the integration tests
//! parse that file rather than trusting the copy here.

use crate::{Answer, FakeFirmware};

/// The config collection this family is spoken to on, and the report id our
/// board's descriptor numbers it with.
pub const CONFIG_USAGE_PAGE: u16 = 0xFF60;
pub const CONFIG_USAGE: u16 = 0x0061;
pub const REPORT_ID: u8 = 9;

/// Frame length excluding the report id, from the board's own descriptor
/// (`95 3F` -- 63 bytes).
pub const FRAME_LEN: usize = 63;

/// Offset of the first data byte, and of the length byte that precedes it.
const LEN_BYTE: usize = 5;
const FIRST_DATA: usize = 6;
const TRAVEL_RECORD: usize = 5;

/// Pads a recorded prefix out to a whole frame and appends its checksum byte.
///
/// The transcripts are written with their long runs of zeroes elided, so this
/// restores the frame's shape. It does **not** compute the checksum: the
/// recorded value is passed in and placed where the board put it.
fn frame(prefix: &[u8], checksum: u8) -> Vec<u8> {
    let mut out = vec![0u8; FRAME_LEN];
    out[..prefix.len()].copy_from_slice(prefix);
    out[FRAME_LEN - 1] = checksum;
    out
}

/// The recorded answer to `read_model_id` (`0x82:0x01`), exchange 001.
fn model_id_answer() -> Vec<u8> {
    frame(
        &[
            0x82, 0x01, 0x00, 0x01, 0x00, 0x06, 0x11, 0x00, 0x00, 0x00, 0x00, 0x05,
        ],
        0x56,
    )
}

/// What one key's actuation record looked like on the board.
///
/// The fifth byte is the one the vendor's decoder ignores and our notes call a
/// pad. It is `01` on the four keys the official configurator had been used on
/// and `00` on the four it had not, which is recorded as an observation and not
/// as a decoded field (exchange 004).
struct KeyRecord {
    key_id: u16,
    travel: u16,
    trailing: u8,
}

/// Every key this fixture can answer for, exactly as the board answered.
const RECORDED_KEYS: &[KeyRecord] = &[
    // Exchange 004: W, A, S, D in this protocol's own numbering.
    KeyRecord {
        key_id: 30,
        travel: 40,
        trailing: 0x01,
    },
    KeyRecord {
        key_id: 43,
        travel: 40,
        trailing: 0x01,
    },
    KeyRecord {
        key_id: 44,
        travel: 40,
        trailing: 0x01,
    },
    KeyRecord {
        key_id: 45,
        travel: 40,
        trailing: 0x01,
    },
    // Exchange 003: the four keys a request built from HID usages reaches
    // instead. Kept precisely so that the wrong request still gets a
    // well-formed answer here, the way it did on the board.
    KeyRecord {
        key_id: 26,
        travel: 79,
        trailing: 0x00,
    }, // `=`
    KeyRecord {
        key_id: 4,
        travel: 79,
        trailing: 0x00,
    }, // F3
    KeyRecord {
        key_id: 22,
        travel: 79,
        trailing: 0x00,
    }, // `8`
    KeyRecord {
        key_id: 7,
        travel: 79,
        trailing: 0x00,
    }, // F6
];

/// A fake HERO 84 HE.
///
/// Answers the three commands this project has sent to the real one and lies
/// about everything else, in the manner the real one lies.
pub struct Aula84He {
    /// Answer `read_travel_precision` with a real precision instead of the echo
    /// the board gives. Off by default, because the default is what was
    /// observed; a test that wants the other branch of `TravelScale` asks for
    /// it explicitly.
    reported_precision: Option<u8>,
    /// Wedge the endpoint on the next write.
    stall_next: bool,
    /// Say nothing to the next request.
    silent_next: bool,
}

impl Default for Aula84He {
    fn default() -> Self {
        Self::observed()
    }
}

impl Aula84He {
    /// The board as it actually behaved. The default, and the one a test should
    /// use unless it is specifically exercising another branch.
    pub fn observed() -> Self {
        Self {
            reported_precision: None,
            stall_next: false,
            silent_next: false,
        }
    }

    /// A hypothetical board in the same family that answers the precision read.
    ///
    /// Not a claim that such a board exists. `TravelScale::Reported` is a branch
    /// our code has and our hardware has never exercised, and a branch nothing
    /// can reach is a branch nobody has checked.
    pub fn reporting_precision(micrometres: u8) -> Self {
        Self {
            reported_precision: Some(micrometres),
            ..Self::observed()
        }
    }

    pub fn stall_on_next_write(&mut self) {
        self.stall_next = true;
    }

    pub fn go_silent_on_next_write(&mut self) {
        self.silent_next = true;
    }

    fn key_travel_answer(&self, request: &[u8]) -> Answer {
        let asked = usize::from(request.get(LEN_BYTE).copied().unwrap_or(0));
        // Two bytes per requested key id, big-endian, as the vendor sends them.
        if asked == 0 || asked % 2 != 0 {
            return Answer::Silence;
        }
        let mut records = Vec::new();
        for pair in request[FIRST_DATA..FIRST_DATA + asked].chunks_exact(2) {
            let key_id = u16::from_be_bytes([pair[0], pair[1]]);
            // A key this board does not have is answered for at all: the board
            // returns a record per id asked, in order, and knowing which ids
            // exist is not something the request can express.
            let recorded = RECORDED_KEYS.iter().find(|k| k.key_id == key_id);
            let (travel, trailing) = match recorded {
                Some(k) => (k.travel, k.trailing),
                None => (79, 0x00),
            };
            records.extend_from_slice(&key_id.to_be_bytes());
            records.extend_from_slice(&travel.to_be_bytes());
            records.push(trailing);
        }

        let mut prefix = vec![0x93, 0x00, 0x00, 0x01, 0x00];
        let Ok(len) = u8::try_from(records.len()) else {
            return Answer::Silence;
        };
        prefix.push(len);
        prefix.extend_from_slice(&records);
        // The checksum recorded for the W/A/S/D answer. For any other set of
        // keys this fixture does not know the real one, and says so by handing
        // back a value that will not match -- our codec records the verdict
        // without enforcing it, so this stays visible rather than fatal.
        let checksum = if records.len() == 4 * TRAVEL_RECORD
            && records.starts_with(&[0x00, 30])
            && records[5..7] == [0x00, 43]
        {
            0x08
        } else if records.len() == 4 * TRAVEL_RECORD && records.starts_with(&[0x00, 26]) {
            0xD7
        } else {
            0x00
        };
        Answer::Recorded(frame(&prefix, checksum))
    }
}

impl FakeFirmware for Aula84He {
    fn report_id(&self) -> u8 {
        REPORT_ID
    }

    fn answer(&mut self, written: &[u8], previous: Option<&[u8]>) -> Answer {
        if std::mem::take(&mut self.stall_next) {
            return Answer::Stall;
        }
        if std::mem::take(&mut self.silent_next) {
            return Answer::Silence;
        }

        match (written.first(), written.get(1)) {
            // read_model_id
            (Some(0x82), Some(0x01)) => Answer::Recorded(model_id_answer()),
            // read_travel_precision
            (Some(0x82), Some(0x08)) => match self.reported_precision {
                None => Answer::EchoedRequest(written.to_vec()),
                Some(micrometres) => {
                    let mut prefix = vec![0x82, 0x08, 0x00, 0x01, 0x00, 0x01];
                    prefix.push(micrometres);
                    Answer::Recorded(frame(&prefix, 0x00))
                }
            },
            // read_key_travel, layer Normal / system Windows only
            (Some(0x93), Some(0x00)) => self.key_travel_answer(written),
            // Everything else. The replay lie, which is what the real firmware
            // does and what the decoder above has to refuse.
            _ => match previous {
                Some(bytes) => Answer::ReplayedPrevious(bytes.to_vec()),
                None => Answer::Silence,
            },
        }
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::expect_used, clippy::panic, clippy::unwrap_used)]

    use super::*;

    fn request(prefix: &[u8]) -> Vec<u8> {
        let mut out = vec![0u8; FRAME_LEN];
        out[..prefix.len()].copy_from_slice(prefix);
        out
    }

    #[test]
    fn the_recorded_model_id_is_the_one_the_board_gave() {
        let mut board = Aula84He::observed();
        let answer = board.answer(&request(&[0x82, 0x01, 0x00, 0x01, 0x00, 0x06]), None);
        let bytes = answer.bytes().expect("an answer");
        assert_eq!(bytes.len(), FRAME_LEN);
        assert_eq!(&bytes[6..12], &[0x11, 0x00, 0x00, 0x00, 0x00, 0x05]);
        assert_eq!(bytes[FRAME_LEN - 1], 0x56, "the checksum the board sent");
    }

    #[test]
    fn the_precision_read_echoes_the_request_the_way_the_board_did() {
        let mut board = Aula84He::observed();
        let sent = request(&[0x82, 0x08, 0x00, 0x01, 0x00, 0x00]);
        let answer = board.answer(&sent, None);
        assert_eq!(answer, Answer::EchoedRequest(sent.clone()));
        assert_eq!(answer.bytes(), Some(sent.as_slice()));
    }

    #[test]
    fn wasd_and_the_hid_usages_get_different_answers() {
        // The property that made the exchange-004 defect findable: the two id
        // sets are not interchangeable, and a fixture that answered them the
        // same could not reproduce the bug that was actually shipped.
        let mut board = Aula84He::observed();
        let wasd = board
            .answer(
                &request(&[
                    0x93, 0x00, 0x00, 0x01, 0x00, 0x08, 0x00, 30, 0x00, 43, 0x00, 44, 0x00, 45,
                ]),
                None,
            )
            .bytes()
            .expect("an answer")
            .to_vec();
        let usages = board
            .answer(
                &request(&[
                    0x93, 0x00, 0x00, 0x01, 0x00, 0x08, 0x00, 0x1A, 0x00, 0x04, 0x00, 0x16, 0x00,
                    0x07,
                ]),
                None,
            )
            .bytes()
            .expect("an answer")
            .to_vec();
        assert_ne!(wasd, usages);
        assert_eq!(
            &wasd[LEN_BYTE..LEN_BYTE + 6],
            &[0x14, 0x00, 30, 0x00, 40, 0x01]
        );
        assert_eq!(
            &usages[LEN_BYTE..LEN_BYTE + 6],
            &[0x14, 0x00, 26, 0x00, 79, 0x00]
        );
    }

    #[test]
    fn the_answer_names_the_keys_in_the_order_asked() {
        let mut board = Aula84He::observed();
        let answer = board
            .answer(
                &request(&[
                    0x93, 0x00, 0x00, 0x01, 0x00, 0x08, 0x00, 45, 0x00, 44, 0x00, 43, 0x00, 30,
                ]),
                None,
            )
            .bytes()
            .expect("an answer")
            .to_vec();
        let ids: Vec<u16> = answer[FIRST_DATA..FIRST_DATA + 20]
            .chunks_exact(TRAVEL_RECORD)
            .map(|r| u16::from_be_bytes([r[0], r[1]]))
            .collect();
        assert_eq!(ids, vec![45, 44, 43, 30]);
    }

    #[test]
    fn a_command_this_board_does_not_know_replays_the_last_answer() {
        let mut board = Aula84He::observed();
        let real = board
            .answer(&request(&[0x82, 0x01, 0x00, 0x01, 0x00, 0x06]), None)
            .bytes()
            .expect("an answer")
            .to_vec();
        // 0x95 is the switch-type read. Recovered from the artifact, never sent,
        // and this board has no answer recorded for it.
        let fiction = board.answer(&request(&[0x95, 0x00]), Some(&real));
        assert_eq!(fiction, Answer::ReplayedPrevious(real));
    }

    #[test]
    fn a_precision_reporting_board_is_available_but_is_not_the_default() {
        assert!(matches!(
            Aula84He::observed().answer(&request(&[0x82, 0x08]), None),
            Answer::EchoedRequest(_)
        ));
        let answer = Aula84He::reporting_precision(10).answer(&request(&[0x82, 0x08]), None);
        assert_eq!(answer.bytes().expect("an answer")[6], 10);
    }
}
