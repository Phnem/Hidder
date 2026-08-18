//! Hall-effect actuation for `aula-bytech`: the reads, and the one scalar that
//! makes their numbers mean millimetres.
//!
//! Recovered statically from the vendor bundle and written up with its
//! provenance in `docs/prior-art/aula-bytech-actuation.md`. Independent
//! implementation of the facts, not a port (ADR-0001, mode `facts`).
//!
//! # The conversion, and why it is not a constant here
//!
//! ```text
//!   travel_mm = travel_raw × (travel_precision / 1000)
//! ```
//!
//! `travel_precision` is a byte the **device** supplies. The vendor reads it
//! once and derives every millimetre figure on its trigger page from it. So
//! this module deliberately has no `0.01` in it: a scale that is read cannot be
//! wrong about a board that uses a different one, and a scale that is assumed
//! can only be right by luck. [`TravelPrecision`] exists as a type so that a
//! raw travel value has nothing to be converted *by* until the device has been
//! asked.
//!
//! # What these values are, and what they are not
//!
//! `travel` here is the **actuation point**: the depth at which a key first
//! registers, an absolute distance from the top of travel. It is not the
//! rapid-trigger sensitivities, which are deltas from a moving reference and
//! live behind a different command; not the calibration extremes, which are raw
//! sensor readings; and not the live key position, which arrives as unsolicited
//! event reports. Four different numbers about how far a key is pressed, and
//! only this one is the setting being read.

use psafety::CommandKey;
use psafety::probe::{ProbeCommandId, ProbeResponse};

use crate::aula_bytech::{MAX_DATA_LEN, ResponseError, decode_response};

/// The report id whose checksum verdict is recorded on responses. Nine on our
/// board; informational only, since the checksum is never enforced.
const REPORT_ID: u8 = 9;

/// Index of the first data byte in a frame. The vendor reads this position
/// regardless of what the frame's length byte claims.
const FIRST_DATA_BYTE: usize = 6;

/// Bytes per record in a key-travel response: id, travel, one pad.
const TRAVEL_RECORD: usize = 5;

/// The vendor's own chunking rule, restated: the request is sized so that the
/// *response* fits one frame. Eleven records of five bytes is 55, one under the
/// 56 a frame carries.
pub const MAX_KEYS_PER_READ: usize = MAX_DATA_LEN / TRAVEL_RECORD;

/// How many micrometres one travel LSB represents, as the device reports it.
///
/// A newtype rather than a bare `u8` so that it cannot be confused with a
/// travel value, a key id, or any other byte this protocol traffics in -- and
/// so that [`Travel::to_millimetres`] cannot be called without one.
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Debug)]
pub struct TravelPrecision(u8);

impl TravelPrecision {
    /// For tests and for showing a reader what a value *would* mean at some
    /// scale. Never a substitute for asking the device.
    pub fn from_micrometres(micrometres: u8) -> Self {
        Self(micrometres)
    }

    pub fn micrometres_per_step(self) -> u8 {
        self.0
    }

    /// The step in millimetres, which is what a UI slider wants.
    pub fn step_mm(self) -> f64 {
        f64::from(self.0) / 1000.0
    }
}

impl std::fmt::Display for TravelPrecision {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{} µm ({} mm per step)", self.0, self.step_mm())
    }
}

/// The step the vendor's own software uses when a board reports no precision.
///
/// Not a number this project chose. The vendor writes its scale as
/// `precision / 1000 || 0.01`, so a zero -- or an absent -- precision byte
/// lands on ten micrometres, and that is what its configurator then displays
/// millimetres with. Recording it as a named constant with its source is the
/// difference between inheriting a documented default and inventing one.
pub const VENDOR_FALLBACK_STEP_MM: f64 = 0.01;

/// What a travel value must be multiplied by, and where that came from.
///
/// Two cases, kept apart because they are not equally strong. A reported
/// precision is the device answering the question. The fallback is the vendor's
/// software answering it on the device's behalf, which is good enough to match
/// that software's display and not good enough to call a device fact.
#[derive(Clone, Copy, PartialEq, Debug)]
pub enum TravelScale {
    /// The device reported a precision.
    Reported(TravelPrecision),
    /// The device reported none, and the vendor's documented fallback applies.
    VendorFallback,
}

impl TravelScale {
    pub fn step_mm(self) -> f64 {
        match self {
            TravelScale::Reported(precision) => precision.step_mm(),
            TravelScale::VendorFallback => VENDOR_FALLBACK_STEP_MM,
        }
    }

    /// Whether the scale came from the device rather than from the fallback.
    pub fn is_from_device(self) -> bool {
        matches!(self, TravelScale::Reported(_))
    }
}

impl std::fmt::Display for TravelScale {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            TravelScale::Reported(precision) => write!(f, "{precision}, reported by the device"),
            TravelScale::VendorFallback => write!(
                f,
                "{VENDOR_FALLBACK_STEP_MM} mm per step, the vendor's fallback (the device reported no precision)"
            ),
        }
    }
}

/// One key's actuation point, still in device units.
///
/// Kept raw deliberately. A travel value alone is not a distance -- it is a
/// count of steps whose size only the device knows -- so the conversion needs a
/// [`TravelPrecision`] and cannot happen by accident.
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Debug)]
pub struct Travel {
    /// HID keyboard usage id, which is what this protocol uses as a key id.
    pub key_id: u16,
    pub raw: u16,
}

impl Travel {
    pub fn to_millimetres(self, scale: TravelScale) -> f64 {
        f64::from(self.raw) * scale.step_mm()
    }
}

// --- read_travel_precision ---------------------------------------------------

#[derive(Clone, Copy, PartialEq, Eq, Debug, thiserror::Error)]
pub enum PrecisionError {
    #[error("frame: {0}")]
    Frame(#[from] ResponseError),
}

/// What the device said about its travel precision.
///
/// # Why a zero is not an error here
///
/// It was, in the first draft, on the reasoning that a zero scale silently
/// turns every actuation point into 0.00 mm. That reasoning was right about the
/// consequence and wrong about the protocol: the vendor reads this byte with the
/// frame's own length ignored and then writes `precision / 1000 || 0.01`, so a
/// zero is a case it handles rather than a fault it reports. Refusing it here
/// would have made our reader stricter than the only implementation known to
/// work with these boards.
#[derive(Clone, Copy, PartialEq, Debug)]
pub struct TravelPrecisionProbe {
    pub scale: TravelScale,
    /// The byte as it arrived, before the fallback was applied. Zero when the
    /// device reported nothing.
    pub raw: u8,
    /// True when the answer was byte-identical to the request.
    ///
    /// Worth surfacing rather than hiding: a frame that echoes the request is
    /// what an unsupported command looks like on boards in this class, and with
    /// an empty payload it is *indistinguishable* from a genuine "no precision
    /// reported". The caller is told, and decides.
    pub echoed_request: bool,
    pub checksum_ok: bool,
}

impl TravelPrecisionProbe {
    /// The 63-byte frame this command sends, for comparing an answer against.
    ///
    /// Rebuilt rather than remembered so that the echo check cannot drift away
    /// from what is actually written.
    fn request_frame_body() -> Vec<u8> {
        crate::aula_bytech::encode_request(
            CommandKey::GroupSubcommand {
                group: 0x82,
                subcommand: 0x08,
            },
            &[],
            REPORT_ID,
        )
        .map(|frame| frame.to_vec())
        .unwrap_or_default()
    }
}

impl ProbeResponse for TravelPrecisionProbe {
    const COMMAND: ProbeCommandId = ProbeCommandId::AulaBytechReadTravelPrecision;
    type Rejection = PrecisionError;

    /// No data. The vendor sends this one empty, unlike the model-id read which
    /// states how many bytes it wants back.
    fn request_payload() -> Vec<u8> {
        Vec::new()
    }

    fn decode(key: CommandKey, response: &[u8]) -> Result<Self, PrecisionError> {
        let decoded = decode_response(key, 0, response, REPORT_ID)?;
        // The vendor forces the length to 1 rather than trusting the frame's
        // own length byte, so read the first data byte whether or not the frame
        // claims to carry one. `decode_response` has already checked that the
        // header echoes this command.
        let raw = response.get(FIRST_DATA_BYTE).copied().unwrap_or(0);
        let scale = if raw == 0 {
            TravelScale::VendorFallback
        } else {
            TravelScale::Reported(TravelPrecision(raw))
        };
        Ok(TravelPrecisionProbe {
            scale,
            raw,
            echoed_request: response == Self::request_frame_body().as_slice(),
            checksum_ok: decoded.checksum_ok,
        })
    }
}

// --- read_key_travel ---------------------------------------------------------

/// The four keys the bootstrap probe reads, as HID keyboard usage ids.
///
/// Fixed in the type rather than passed in, and that is the point: a bootstrap
/// probe is a pre-declared operation a person agreed to, not a parameterised
/// capability. W, A, S and D are the keys a person can most easily check
/// against the vendor's own configurator.
pub const WASD_KEY_IDS: [u16; 4] = [0x1A, 0x04, 0x16, 0x07];

#[derive(Clone, Copy, PartialEq, Eq, Debug, thiserror::Error)]
pub enum KeyTravelError {
    #[error("frame: {0}")]
    Frame(#[from] ResponseError),
    #[error(
        "response carries {len} data bytes, which is not a whole number of {TRAVEL_RECORD}-byte records"
    )]
    NotWholeRecords { len: usize },
    #[error("asked for {asked} keys, got {got} records back")]
    WrongRecordCount { asked: usize, got: usize },
    #[error(
        "record {index} answers for key {found:#06x}, not the {expected:#06x} that was asked for"
    )]
    UnexpectedKey {
        index: usize,
        expected: u16,
        found: u16,
    },
}

/// Actuation points for the four bootstrap keys, still in device units.
#[derive(Clone, PartialEq, Eq, Debug)]
pub struct WasdTravelProbe {
    pub travels: Vec<Travel>,
    pub checksum_ok: bool,
}

impl ProbeResponse for WasdTravelProbe {
    const COMMAND: ProbeCommandId = ProbeCommandId::AulaBytechReadKeyTravel;
    type Rejection = KeyTravelError;

    fn request_payload() -> Vec<u8> {
        WASD_KEY_IDS
            .iter()
            .flat_map(|id| id.to_be_bytes())
            .collect()
    }

    fn decode(key: CommandKey, response: &[u8]) -> Result<Self, KeyTravelError> {
        let decoded = decode_response(key, WASD_KEY_IDS.len() * 2, response, REPORT_ID)?;
        let data = &decoded.data;

        if data.len() % TRAVEL_RECORD != 0 {
            return Err(KeyTravelError::NotWholeRecords { len: data.len() });
        }
        let got = data.len() / TRAVEL_RECORD;
        if got != WASD_KEY_IDS.len() {
            return Err(KeyTravelError::WrongRecordCount {
                asked: WASD_KEY_IDS.len(),
                got,
            });
        }

        let mut travels = Vec::with_capacity(got);
        for (index, chunk) in data.chunks_exact(TRAVEL_RECORD).enumerate() {
            let key_id = u16::from_be_bytes([chunk[0], chunk[1]]);
            let expected = WASD_KEY_IDS[index];
            // The device is expected to answer in the order asked. Checking it
            // is what turns "five bytes arrived" into "this is W's setting":
            // a reply in a different order, silently zipped against our own
            // list, would attribute one key's value to another.
            if key_id != expected {
                return Err(KeyTravelError::UnexpectedKey {
                    index,
                    expected,
                    found: key_id,
                });
            }
            travels.push(Travel {
                key_id,
                raw: u16::from_be_bytes([chunk[2], chunk[3]]),
            });
        }

        Ok(WasdTravelProbe {
            travels,
            checksum_ok: decoded.checksum_ok,
        })
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::expect_used, clippy::panic, clippy::unwrap_used)]

    use super::*;
    use crate::aula_bytech::{FRAME_LEN, checksum};

    fn frame(group: u8, subcommand: u8, data: &[u8]) -> [u8; FRAME_LEN] {
        let mut f = [0u8; FRAME_LEN];
        f[0] = group;
        f[1] = subcommand;
        f[3] = 1;
        f[4] = 0;
        #[allow(clippy::cast_possible_truncation)]
        {
            f[5] = data.len() as u8;
        }
        f[6..6 + data.len()].copy_from_slice(data);
        f[FRAME_LEN - 1] = checksum(REPORT_ID, &f);
        f
    }

    fn precision_key() -> CommandKey {
        CommandKey::GroupSubcommand {
            group: 0x82,
            subcommand: 0x08,
        }
    }

    fn travel_key() -> CommandKey {
        CommandKey::GroupSubcommand {
            group: 0x93,
            subcommand: 0x00,
        }
    }

    fn travel_records(values: &[(u16, u16)]) -> Vec<u8> {
        values
            .iter()
            .flat_map(|(id, raw)| {
                let mut record = Vec::with_capacity(TRAVEL_RECORD);
                record.extend_from_slice(&id.to_be_bytes());
                record.extend_from_slice(&raw.to_be_bytes());
                record.push(0);
                record
            })
            .collect()
    }

    // --- the conversion ---------------------------------------------------

    #[test]
    fn a_travel_value_has_no_millimetres_without_a_scale() {
        // Stated as a test because it is the whole design: `Travel` carries no
        // scale, so the only way to millimetres is through one that was either
        // reported or explicitly fallen back to. If `to_millimetres` ever loses
        // its argument, this comment is why it must not.
        let travel = Travel {
            key_id: 0x1A,
            raw: 40,
        };
        let at = |micrometres| {
            travel.to_millimetres(TravelScale::Reported(TravelPrecision(micrometres)))
        };
        assert_eq!(at(10), 0.40);
        assert_eq!(at(100), 4.0);
        assert_eq!(at(1), 0.04);
        assert_eq!(travel.to_millimetres(TravelScale::VendorFallback), 0.40);
    }

    #[test]
    fn the_step_is_the_precision_in_millimetres() {
        assert_eq!(TravelPrecision(10).step_mm(), 0.01);
        assert_eq!(TravelPrecision(50).step_mm(), 0.05);
    }

    // --- read_travel_precision --------------------------------------------

    #[test]
    fn a_reported_precision_decodes() {
        let decoded = TravelPrecisionProbe::decode(precision_key(), &frame(0x82, 0x08, &[10]))
            .expect("a one-byte answer");
        assert_eq!(decoded.scale, TravelScale::Reported(TravelPrecision(10)));
        assert!(decoded.scale.is_from_device());
        assert!(!decoded.echoed_request);
        assert!(decoded.checksum_ok);
    }

    #[test]
    fn a_zero_precision_falls_back_the_way_the_vendor_does() {
        // The case our own board produces. The vendor writes `x / 1000 || 0.01`,
        // so zero is handled rather than refused, and its configurator displays
        // millimetres computed with 0.01 either way.
        let decoded = TravelPrecisionProbe::decode(precision_key(), &frame(0x82, 0x08, &[0]))
            .expect("zero is a case, not a fault");
        assert_eq!(decoded.scale, TravelScale::VendorFallback);
        assert_eq!(decoded.scale.step_mm(), 0.01);
        assert!(!decoded.scale.is_from_device());
    }

    #[test]
    fn an_answer_identical_to_the_request_is_flagged_as_an_echo() {
        // What our board actually returned for this command: our own frame,
        // byte for byte. With an empty payload that is indistinguishable from a
        // genuine "no precision reported", so the decoder reports both facts and
        // refuses to decide between them.
        let echo = TravelPrecisionProbe::request_frame_body();
        let decoded = TravelPrecisionProbe::decode(precision_key(), &echo)
            .expect("an echo still parses as a frame");
        assert!(decoded.echoed_request, "the echo was not noticed");
        assert_eq!(decoded.raw, 0);
        assert_eq!(decoded.scale, TravelScale::VendorFallback);
    }

    #[test]
    fn a_precision_answer_to_another_command_is_refused() {
        assert!(matches!(
            TravelPrecisionProbe::decode(precision_key(), &frame(0x82, 0x01, &[10])),
            Err(PrecisionError::Frame(
                ResponseError::SubcommandMismatch { .. }
            ))
        ));
    }

    // --- read_key_travel ---------------------------------------------------

    #[test]
    fn four_records_decode_in_the_order_asked() {
        let data = travel_records(&[(0x1A, 40), (0x04, 40), (0x16, 30), (0x07, 25)]);
        let decoded =
            WasdTravelProbe::decode(travel_key(), &frame(0x93, 0x00, &data)).expect("well formed");
        assert_eq!(decoded.travels.len(), 4);
        assert_eq!(decoded.travels[0].key_id, 0x1A);
        assert_eq!(decoded.travels[0].raw, 40);
        assert_eq!(decoded.travels[3].key_id, 0x07);
        assert_eq!(decoded.travels[3].raw, 25);
        assert_eq!(
            decoded.travels[0].to_millimetres(TravelScale::Reported(TravelPrecision(10))),
            0.40
        );
    }

    #[test]
    fn records_answering_for_the_wrong_keys_are_refused() {
        // The failure this prevents: the device answers in a different order,
        // the values get zipped against our own list, and one key's setting is
        // reported as another's. Nothing about the frame would look wrong.
        let data = travel_records(&[(0x04, 40), (0x1A, 40), (0x16, 30), (0x07, 25)]);
        assert!(matches!(
            WasdTravelProbe::decode(travel_key(), &frame(0x93, 0x00, &data)),
            Err(KeyTravelError::UnexpectedKey {
                index: 0,
                expected: 0x1A,
                found: 0x04
            })
        ));
    }

    #[test]
    fn a_short_answer_is_refused_rather_than_padded() {
        let data = travel_records(&[(0x1A, 40), (0x04, 40)]);
        assert_eq!(
            WasdTravelProbe::decode(travel_key(), &frame(0x93, 0x00, &data)),
            Err(KeyTravelError::WrongRecordCount { asked: 4, got: 2 })
        );
    }

    #[test]
    fn a_ragged_answer_is_refused() {
        let mut data = travel_records(&[(0x1A, 40), (0x04, 40), (0x16, 30), (0x07, 25)]);
        data.push(0);
        assert_eq!(
            WasdTravelProbe::decode(travel_key(), &frame(0x93, 0x00, &data)),
            Err(KeyTravelError::NotWholeRecords { len: 21 })
        );
    }

    #[test]
    fn the_request_names_exactly_the_four_keys() {
        assert_eq!(
            WasdTravelProbe::request_payload(),
            vec![0x00, 0x1A, 0x00, 0x04, 0x00, 0x16, 0x00, 0x07]
        );
    }

    #[test]
    fn the_bootstrap_request_fits_one_frame_and_so_does_its_answer() {
        assert!(WASD_KEY_IDS.len() <= MAX_KEYS_PER_READ);
        assert_eq!(MAX_KEYS_PER_READ, 11, "the vendor's own chunk size");
        assert!(WASD_KEY_IDS.len() * TRAVEL_RECORD <= MAX_DATA_LEN);
    }
}
