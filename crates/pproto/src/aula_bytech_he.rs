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

use psafety::{CommandKey, CommandResponse, SafeCommandId};

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

/// A key's identifier **in this protocol's own numbering**, which is not a HID
/// usage.
///
/// # The mistake this type exists to make impossible
///
/// It has already been made once, on hardware, and nothing about the exchange
/// looked wrong. The first `read_key_travel` probe asked for W, A, S and D by
/// their HID keyboard usages -- 0x1A, 0x04, 0x16, 0x07 -- because "key id" was
/// read as "the number that identifies a key". The board answered without
/// complaint, in request order, with a well-formed record per id and a matching
/// checksum. Every check the decoder performs passed. It had simply reported the
/// actuation of `=`, F3, `8` and F6, which are the keys those four numbers name
/// in *this* numbering, and which nobody had changed
/// (docs/hardware/aula-bytech-exchange-003-actuation.md, and -004 for the
/// correction).
///
/// So the two spaces are separate types from here on. They are both `u16`, both
/// small, both called something like "key id" in casual speech, and confusing
/// them produces a confident wrong answer rather than an error -- which is the
/// exact profile of a bug that a newtype is worth the trouble of carrying.
///
/// # Where each number lives
///
/// | Space | Vendor's name | Which commands carry it |
/// |---|---|---|
/// | this one | `keyValue` | every per-key performance command: travel, rapid trigger, dead zone, switch type |
/// | HID usage | `keycode` / `browserValue` | the keymap command only, as a *value* rather than as an identifier |
///
/// The vendor's own layout tables are indexed by this number, and its keymap
/// read returns records of `{ id, keycode }` -- two distinct fields in one
/// record, which is the artifact stating outright that they are not the same
/// thing (docs/prior-art/aula-bytech-actuation.md).
///
/// # Scope
///
/// The mapping belongs to the `bytech` family, not to a board: the vendor
/// carries one key table for the family and one layout per model that selects
/// which of those keys physically exist. So [`KeyId::W`] is W on every board in
/// the family; whether a given board *has* that key is what the layout answers.
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Debug)]
pub struct KeyId(u16);

impl KeyId {
    /// The four keys the actuation read names, from the vendor's key table for
    /// this family.
    ///
    /// Written as named constants rather than as literals in an array so that
    /// the number and the key it means cannot drift apart silently again.
    pub const W: KeyId = KeyId(30);
    pub const A: KeyId = KeyId(43);
    pub const S: KeyId = KeyId(44);
    pub const D: KeyId = KeyId(45);

    /// For decoding a record that came off the wire, and for tests.
    ///
    /// Deliberately not `From<u16>`: constructing one should look like a
    /// decision, because a `u16` from the wrong space converts just as happily
    /// as one from the right space.
    pub fn from_wire(raw: u16) -> Self {
        Self(raw)
    }

    pub fn raw(self) -> u16 {
        self.0
    }
}

impl std::fmt::Display for KeyId {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "key {}", self.0)
    }
}

/// One key's actuation point, still in device units.
///
/// Kept raw deliberately. A travel value alone is not a distance -- it is a
/// count of steps whose size only the device knows -- so the conversion needs a
/// [`TravelPrecision`] and cannot happen by accident.
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Debug)]
pub struct Travel {
    /// Which key this is, in this protocol's numbering. See [`KeyId`] for why
    /// that is a type rather than a `u16`.
    pub key_id: KeyId,
    pub raw: u16,
}

impl Travel {
    pub fn to_millimetres(self, scale: TravelScale) -> f64 {
        f64::from(self.raw) * scale.step_mm()
    }
}

// --- read_travel_precision, and why there is no code for it ------------------
//
// `0x82:0x08` was sent to this board twice and returned our own frame byte for
// byte both times. That is what an unsupported command looks like on boards in
// this class, and with an empty payload it cannot be told apart from a genuine
// "nothing to report" (docs/hardware/aula-bytech-exchange-003-actuation.md).
//
// So it is classified `unknown` in the ACL, which is the class the generator
// emits no command id for -- neither a `SafeCommandId` nor a `ProbeCommandId`.
// There is therefore no way to send it, and a decoder for a command that cannot
// be sent would be a type implying a capability this board has not shown.
//
// What survives is the part that was actually learned: the vendor's fallback,
// as [`TravelScale`] above. The device reported no precision, the vendor's own
// software computes 0.01 mm per step in that case, and exchange 005 confirmed
// that scale against four values a person set in the vendor's configurator. The
// scale is still a two-case type rather than a constant, because a different
// board in this family may well answer -- and if one ever does, this is where
// the decoder comes back.

// --- read_key_travel ---------------------------------------------------------

/// The four keys the actuation read names, in this protocol's numbering.
///
/// Fixed in the type rather than passed in, and that is the point: this is a
/// pre-declared operation a person agreed to, not a parameterised capability.
/// W, A, S and D are the keys a person can most easily set to four different
/// values in the vendor's own configurator and check against.
pub const WASD_KEY_IDS: [KeyId; 4] = [KeyId::W, KeyId::A, KeyId::S, KeyId::D];

/// Human labels for [`WASD_KEY_IDS`], in the same order, for reports and the UI.
pub const WASD_KEY_LABELS: [&str; 4] = ["W", "A", "S", "D"];

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
    #[error("record {index} answers for {found}, not the {expected} that was asked for")]
    UnexpectedKey {
        index: usize,
        expected: KeyId,
        found: KeyId,
    },
}

/// Actuation points for W, A, S and D, still in device units.
///
/// A production read since 2026-08-18. It arrived through the bootstrap door as
/// a probe, was checked against four different values set in the official
/// configurator, and left through the front
/// (docs/hardware/aula-bytech-exchange-005-actuation-verified.md). The type is
/// the same shape it was; what changed is which gate it reaches, and that is a
/// consequence of the ACL entry rather than of anything written here.
#[derive(Clone, PartialEq, Eq, Debug)]
pub struct WasdTravelRead {
    pub travels: Vec<Travel>,
    pub checksum_ok: bool,
}

impl CommandResponse for WasdTravelRead {
    const COMMAND: SafeCommandId = SafeCommandId::AulaBytechReadKeyTravel;
    type Rejection = KeyTravelError;

    fn request_payload() -> Vec<u8> {
        WASD_KEY_IDS
            .iter()
            .flat_map(|id| id.raw().to_be_bytes())
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
            let key_id = KeyId::from_wire(u16::from_be_bytes([chunk[0], chunk[1]]));
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

        Ok(WasdTravelRead {
            travels,
            checksum_ok: decoded.checksum_ok,
        })
    }
}

// --- the canonical capability -------------------------------------------------

/// How many decimals of a millimetre this scale can actually distinguish.
///
/// Derived from the step rather than chosen: at 0.01 mm per raw unit two
/// decimals are meaningful and a third would be invented. The vendor computes
/// its own displayed precision the same way, from the same scalar.
fn decimals_for(scale: TravelScale) -> u8 {
    let step = scale.step_mm();
    let mut decimals = 0u8;
    let mut scaled = step;
    while scaled < 1.0 && decimals < 6 {
        scaled *= 10.0;
        decimals += 1;
    }
    decimals
}

/// Turns a protocol read into the canonical `he.actuation` capability.
///
/// This is the point where protocol detail stops. Above it there are labelled
/// keys and millimetres; below it there are vendor key ids, raw counts and a
/// scale that had to be established. The layers above must not be able to see
/// through this boundary -- a UI that received a vendor key id would need the
/// vendor's key table to render it, and a UI that received a raw count would
/// eventually display one.
pub fn actuation_capability(read: &WasdTravelRead, scale: TravelScale) -> pcaps::Capability {
    let decimals = decimals_for(scale);
    let values = read
        .travels
        .iter()
        .zip(WASD_KEY_LABELS)
        .map(|(travel, label)| pcaps::KeyMeasurement {
            label: label.to_string(),
            measurement: pcaps::Measurement::new(
                travel.to_millimetres(scale),
                pcaps::Unit::Millimetres,
                decimals,
            ),
        })
        .collect();

    pcaps::Capability {
        id: pcaps::CapId::HeActuation,
        origin: pcaps::Origin::VerifiedOnHardware {
            command: "read_key_travel",
        },
        confidence: pcaps::Confidence::Verified,
        value: pcaps::CapValue::PerKey(values),
        provenance: provenance(scale),
    }
}

/// One line saying why these numbers mean what they say.
///
/// Written out rather than assembled from debug formatting, because it is shown
/// to a person who is deciding whether to trust a control, and it has to be
/// honest about the weaker case: a scale the device did not report is the
/// vendor's default, and saying so is the difference between inheriting a
/// documented default and quietly inventing one.
fn provenance(scale: TravelScale) -> String {
    match scale {
        TravelScale::Reported(precision) => format!(
            "read with command read_key_travel; scale {} reported by the device",
            precision
        ),
        TravelScale::VendorFallback => [
            format!(
                "read with command read_key_travel; scale {VENDOR_FALLBACK_STEP_MM} mm per step,"
            ),
            "the vendor's documented fallback for a device that reports none,".to_string(),
            "confirmed against four values set in the official configurator on 2026-08-18"
                .to_string(),
        ]
        .join(" "),
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::expect_used, clippy::panic, clippy::unwrap_used)]

    use super::*;
    use crate::aula_bytech::{FRAME_LEN, checksum};
    use pcaps::CapValue;

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

    fn travel_key() -> CommandKey {
        CommandKey::GroupSubcommand {
            group: 0x93,
            subcommand: 0x00,
        }
    }

    fn travel_records(values: &[(KeyId, u16)]) -> Vec<u8> {
        values
            .iter()
            .flat_map(|(id, raw)| {
                let mut record = Vec::with_capacity(TRAVEL_RECORD);
                record.extend_from_slice(&id.raw().to_be_bytes());
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
            key_id: KeyId::W,
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

    // --- read_key_travel ---------------------------------------------------

    #[test]
    fn four_records_decode_in_the_order_asked() {
        let data = travel_records(&[
            (KeyId::W, 50),
            (KeyId::A, 100),
            (KeyId::S, 150),
            (KeyId::D, 200),
        ]);
        let decoded =
            WasdTravelRead::decode(travel_key(), &frame(0x93, 0x00, &data)).expect("well formed");
        assert_eq!(decoded.travels.len(), 4);
        assert_eq!(decoded.travels[0].key_id, KeyId::W);
        assert_eq!(decoded.travels[0].raw, 50);
        assert_eq!(decoded.travels[3].key_id, KeyId::D);
        assert_eq!(decoded.travels[3].raw, 200);
        assert_eq!(
            decoded.travels[0].to_millimetres(TravelScale::Reported(TravelPrecision(10))),
            0.50
        );
    }

    #[test]
    fn records_answering_for_the_wrong_keys_are_refused() {
        // The failure this prevents: the device answers in a different order,
        // the values get zipped against our own list, and one key's setting is
        // reported as another's. Nothing about the frame would look wrong.
        let data = travel_records(&[
            (KeyId::A, 40),
            (KeyId::W, 40),
            (KeyId::S, 30),
            (KeyId::D, 25),
        ]);
        assert!(matches!(
            WasdTravelRead::decode(travel_key(), &frame(0x93, 0x00, &data)),
            Err(KeyTravelError::UnexpectedKey {
                index: 0,
                expected: KeyId::W,
                found: KeyId::A
            })
        ));
    }

    #[test]
    fn a_short_answer_is_refused_rather_than_padded() {
        let data = travel_records(&[(KeyId::W, 40), (KeyId::A, 40)]);
        assert_eq!(
            WasdTravelRead::decode(travel_key(), &frame(0x93, 0x00, &data)),
            Err(KeyTravelError::WrongRecordCount { asked: 4, got: 2 })
        );
    }

    #[test]
    fn a_ragged_answer_is_refused() {
        let mut data = travel_records(&[
            (KeyId::W, 40),
            (KeyId::A, 40),
            (KeyId::S, 30),
            (KeyId::D, 25),
        ]);
        data.push(0);
        assert_eq!(
            WasdTravelRead::decode(travel_key(), &frame(0x93, 0x00, &data)),
            Err(KeyTravelError::NotWholeRecords { len: 21 })
        );
    }

    #[test]
    fn the_request_names_exactly_the_four_keys() {
        // The literal bytes, spelled out, because this is the assertion that
        // would have caught the exchange-003 defect: 30, 43, 44, 45 are W A S D
        // in this protocol's numbering, and 26, 4, 22, 7 -- their HID usages --
        // are `=`, F3, `8` and F6 on the very same board.
        assert_eq!(
            WasdTravelRead::request_payload(),
            vec![0x00, 30, 0x00, 43, 0x00, 44, 0x00, 45]
        );
    }

    #[test]
    fn a_key_id_is_not_a_hid_usage() {
        // The four HID usages that were sent by mistake, and what they name in
        // the space this command actually uses. If someone "fixes" KeyId back to
        // the usage values, every one of these becomes an equality and this test
        // fails, which is the point.
        for (usage, key) in [
            (0x1Au16, KeyId::W),
            (0x04, KeyId::A),
            (0x16, KeyId::S),
            (0x07, KeyId::D),
        ] {
            assert_ne!(
                usage,
                key.raw(),
                "{key} was given its HID usage as its protocol key id"
            );
        }
        assert_eq!(
            [KeyId::W, KeyId::A, KeyId::S, KeyId::D].map(KeyId::raw),
            [30, 43, 44, 45],
            "the vendor's own key table for the bytech family"
        );
    }

    #[test]
    fn the_labels_line_up_with_the_ids() {
        assert_eq!(WASD_KEY_IDS.len(), WASD_KEY_LABELS.len());
        assert_eq!(WASD_KEY_LABELS, ["W", "A", "S", "D"]);
    }

    #[test]
    fn the_bootstrap_request_fits_one_frame_and_so_does_its_answer() {
        assert!(WASD_KEY_IDS.len() <= MAX_KEYS_PER_READ);
        assert_eq!(MAX_KEYS_PER_READ, 11, "the vendor's own chunk size");
        assert!(WASD_KEY_IDS.len() * TRAVEL_RECORD <= MAX_DATA_LEN);
    }
    // --- the canonical capability -----------------------------------------

    fn wasd_read(raws: [u16; 4]) -> WasdTravelRead {
        WasdTravelRead {
            travels: WASD_KEY_IDS
                .iter()
                .zip(raws)
                .map(|(id, raw)| Travel { key_id: *id, raw })
                .collect(),
            checksum_ok: true,
        }
    }

    #[test]
    fn the_capability_converts_the_exchange_005_values() {
        // The four values the official configurator wrote and this project read
        // back, as they reach the layer above. If the conversion ever drifts,
        // this is the test that disagrees with the hardware record.
        let capability =
            actuation_capability(&wasd_read([51, 102, 149, 200]), TravelScale::VendorFallback);
        let CapValue::PerKey(keys) = &capability.value else {
            panic!("actuation is per-key");
        };
        let rendered: Vec<(String, String)> = keys
            .iter()
            .map(|k| (k.label.clone(), k.measurement.render()))
            .collect();
        assert_eq!(
            rendered,
            vec![
                ("W".to_string(), "0.51 mm".to_string()),
                ("A".to_string(), "1.02 mm".to_string()),
                ("S".to_string(), "1.49 mm".to_string()),
                ("D".to_string(), "2.00 mm".to_string()),
            ]
        );
    }

    #[test]
    fn the_labels_follow_the_keys_and_not_the_order_of_arrival() {
        // W is the first key asked for, so W must be the first label. A mapping
        // that drifted by one would put A's value under W and look entirely
        // plausible while doing it.
        let capability =
            actuation_capability(&wasd_read([10, 20, 30, 40]), TravelScale::VendorFallback);
        let CapValue::PerKey(keys) = &capability.value else {
            panic!("per-key");
        };
        assert_eq!(
            keys.iter().map(|k| k.label.as_str()).collect::<Vec<_>>(),
            vec!["W", "A", "S", "D"]
        );
        assert_eq!(keys[0].measurement.render(), "0.10 mm");
        assert_eq!(keys[3].measurement.render(), "0.40 mm");
    }

    #[test]
    fn the_provenance_says_which_scale_applied_and_where_it_came_from() {
        // Two cases that are not equally strong, and the difference has to
        // survive all the way to something a person reads.
        let fallback =
            actuation_capability(&wasd_read([51, 51, 51, 51]), TravelScale::VendorFallback);
        assert!(fallback.provenance.contains("fallback"));
        assert!(fallback.provenance.contains("read_key_travel"));

        let reported = actuation_capability(
            &wasd_read([51, 51, 51, 51]),
            TravelScale::Reported(TravelPrecision::from_micrometres(10)),
        );
        assert!(reported.provenance.contains("reported by the device"));
        assert!(
            !reported.provenance.contains("fallback"),
            "a device-reported scale must not be described as a fallback"
        );
    }

    #[test]
    fn a_reported_scale_that_differs_changes_the_millimetres() {
        // The reason the scale is a value rather than a constant: a board that
        // reports a different precision must produce different millimetres from
        // the same raw counts.
        let coarse = actuation_capability(
            &wasd_read([51, 51, 51, 51]),
            TravelScale::Reported(TravelPrecision::from_micrometres(100)),
        );
        let CapValue::PerKey(keys) = &coarse.value else {
            panic!("per-key");
        };
        // Compared as rendered rather than as a float, and that is the point
        // rather than a convenience: 51 x 0.1 is 5.1000000000000005 in binary
        // floating point, and what a person sees is the rendering. Asserting on
        // the bits would be asserting on something no user can observe -- and
        // would pass or fail for reasons that have nothing to do with the
        // protocol.
        assert_eq!(keys[0].measurement.render(), "5.1 mm");
        assert_eq!(
            keys[0].measurement.decimals, 1,
            "a 0.1 mm step distinguishes one decimal, not two"
        );
    }

    #[test]
    fn the_decimals_come_from_the_step_rather_than_from_taste() {
        assert_eq!(decimals_for(TravelScale::VendorFallback), 2);
        assert_eq!(
            decimals_for(TravelScale::Reported(TravelPrecision::from_micrometres(
                100
            ))),
            1
        );
        assert_eq!(
            decimals_for(TravelScale::Reported(TravelPrecision::from_micrometres(1))),
            3
        );
    }

    #[test]
    fn the_capability_is_verified_and_names_the_command() {
        let capability =
            actuation_capability(&wasd_read([51, 51, 51, 51]), TravelScale::VendorFallback);
        assert_eq!(capability.id, pcaps::CapId::HeActuation);
        assert_eq!(capability.confidence, pcaps::Confidence::Verified);
        assert_eq!(
            capability.origin,
            pcaps::Origin::VerifiedOnHardware {
                command: "read_key_travel"
            }
        );
    }

    #[test]
    fn no_protocol_key_id_reaches_the_capability() {
        // The boundary. A vendor key id above this line would need the vendor's
        // key table to render, which is the coupling this layer exists to stop
        // -- and conflating two key-number spaces has already cost this project
        // one hardware exchange.
        let capability =
            actuation_capability(&wasd_read([51, 102, 149, 200]), TravelScale::VendorFallback);
        let CapValue::PerKey(keys) = &capability.value else {
            panic!("per-key");
        };
        for key in keys {
            assert!(
                key.label.parse::<u16>().is_err(),
                "a raw key id leaked out as a label: {}",
                key.label
            );
        }
        let rendered = format!("{capability:?}");
        for id in WASD_KEY_IDS {
            assert!(
                !rendered.contains(&format!("KeyId({})", id.raw())),
                "a KeyId reached the capability value: {rendered}"
            );
        }
    }
}
