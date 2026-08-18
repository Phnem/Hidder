//! The `aula-bytech` frame format, and the one command this project may send.
//!
//! Every number here was recovered from the vendor's own configurator bundle by
//! static analysis and written up, with its provenance, in
//! `docs/prior-art/aula-bytech-readdeviceuuid.md`. Nothing was copied: the
//! facts are the frame layout, the checksum arithmetic and the offsets, and
//! this is an independent implementation of them (ADR-0001, mode `facts`).
//!
//! # The frame
//!
//! 63 bytes each way. The HID report id is prepended by the write, making 64 on
//! the wire, and our own report descriptor for `0xFF60:0x0061` agrees: report id
//! 9, `95 3F` -- 63 -- for both the input and the output item.
//!
//! ```text
//!   index  0   command group
//!          1   subcommand
//!          2   reserved, written as zero
//!          3   total packet count
//!          4   this packet's index, zero-based
//!          5   number of data bytes in this packet
//!       6..61  data, zero-padded
//!         62   checksum
//! ```
//!
//! # The checksum is not circular, and not the mouse stack's
//!
//! The builder computes it over the frame *with the checksum slot already
//! zeroed*, and seeds the sum with the report id:
//!
//! ```text
//!   frame[62] = 0
//!   checksum  = 0xFF - ((report_id + sum(frame[0..=62])) & 0xFF)
//!   frame[62] = checksum
//! ```
//!
//! Since `frame[62]` is zero while the sum runs, that is the same as summing
//! `frame[0..=61]`, and [`checksum`] is written the second way so no reader has
//! to notice. The finished frame satisfies one invariant, which is the cheapest
//! way to state the whole thing:
//!
//! ```text
//!   (report_id + sum(frame[0..=62])) & 0xFF == 0xFF
//! ```
//!
//! Two properties are worth stating out loud because they are what makes this
//! *not* the same algorithm as the vendor's mouse stack, whose frames are also
//! 63 bytes: this one is a negation rather than a truncated sum, it lives at the
//! end of the frame rather than the start, and **the report id participates**,
//! so a frame is only valid for the report id it was built for. The shared
//! packet size is a coincidence of size; the framing is not shared, and the
//! mouse stack's arithmetic was not consulted while writing this.

use psafety::{CommandKey, ProbeCommandId, ProbeResponse};

/// Total bytes in one frame, excluding the report id the HID write prepends.
pub const FRAME_LEN: usize = 63;
/// Bytes before the data area.
pub const HEADER_LEN: usize = 6;
/// Bytes after the data area: the checksum.
pub const TAIL_LEN: usize = 1;
/// The most data one frame can carry.
pub const MAX_DATA_LEN: usize = FRAME_LEN - HEADER_LEN - TAIL_LEN;

const GROUP: usize = 0;
const SUBCOMMAND: usize = 1;
const RESERVED: usize = 2;
const TOTAL_PACKETS: usize = 3;
const PACKET_INDEX: usize = 4;
const DATA_LEN: usize = 5;
const DATA: usize = HEADER_LEN;
const CHECKSUM: usize = FRAME_LEN - 1;

/// The checksum byte for a frame, computed as the builder computes it.
///
/// Takes the frame with the checksum slot at any value: the slot is excluded
/// from the sum rather than assumed to be zero, so this is total and has no
/// order-of-operations trap.
pub fn checksum(report_id: u8, frame: &[u8; FRAME_LEN]) -> u8 {
    let sum: u32 = frame[..CHECKSUM].iter().map(|b| u32::from(*b)).sum();
    let seeded = sum.wrapping_add(u32::from(report_id));
    #[allow(clippy::cast_possible_truncation)]
    let low = (seeded & 0xFF) as u8;
    0xFF - low
}

/// Whether a frame's checksum byte is the one [`checksum`] would produce.
///
/// Used on responses, and deliberately not used to *reject* one. The vendor's
/// own driver never checks an incoming checksum, so whether the device computes
/// it the same way -- same negation, same report-id seed -- is unverified. Until
/// an exchange settles that, a mismatch is something to record, not something to
/// treat as a failure.
pub fn checksum_matches(report_id: u8, frame: &[u8; FRAME_LEN]) -> bool {
    frame[CHECKSUM] == checksum(report_id, frame)
}

/// Why a request could not be built.
#[derive(Clone, Copy, PartialEq, Eq, Debug, thiserror::Error)]
pub enum EncodeError {
    /// The command is addressed as a bare opcode, which this family does not
    /// use. Not a fallback case: a family whose commands are pairs has no
    /// meaning for a single byte, and inventing one would put an unclassified
    /// subcommand on the wire.
    #[error("aula-bytech addresses commands as a group and a subcommand, not as a bare opcode")]
    NotAGroupSubcommandPair,
    /// More data than one frame carries. Multi-packet requests exist in this
    /// protocol and are deliberately not implemented here: the probe path sends
    /// one frame, and a chunked request is several.
    #[error("payload of {len} bytes exceeds the {MAX_DATA_LEN} a single frame carries")]
    PayloadTooLong { len: usize },
}

/// Builds the single-frame request for one command.
///
/// Single-frame only, and that is a scope decision rather than an omission. The
/// vendor's builder chunks longer payloads across several frames; the probe path
/// sends exactly one, so a payload that would need chunking is an error here
/// rather than a silent second write.
pub fn encode_request(
    key: CommandKey,
    payload: &[u8],
    report_id: u8,
) -> Result<[u8; FRAME_LEN], EncodeError> {
    let CommandKey::GroupSubcommand { group, subcommand } = key else {
        return Err(EncodeError::NotAGroupSubcommandPair);
    };
    if payload.len() > MAX_DATA_LEN {
        return Err(EncodeError::PayloadTooLong { len: payload.len() });
    }

    let mut frame = [0u8; FRAME_LEN];
    frame[GROUP] = group;
    frame[SUBCOMMAND] = subcommand;
    frame[RESERVED] = 0;
    frame[TOTAL_PACKETS] = 1;
    frame[PACKET_INDEX] = 0;
    // Fits in a byte because it is at most MAX_DATA_LEN, checked above.
    #[allow(clippy::cast_possible_truncation)]
    let len = payload.len() as u8;
    frame[DATA_LEN] = len;
    frame[DATA..DATA + payload.len()].copy_from_slice(payload);
    frame[CHECKSUM] = checksum(report_id, &frame);
    Ok(frame)
}

/// Why a response was not an answer to the command that was sent.
///
/// Every variant is a reason to stop rather than to adjust anything. An
/// unsupported command on boards in this class often replays the previous
/// answer, so a response that does not match is evidence *against* the command
/// meaning what we believed, not a hint to try a neighbouring one.
#[derive(Clone, Copy, PartialEq, Eq, Debug, thiserror::Error)]
pub enum ResponseError {
    #[error("response is {len} bytes; this protocol's frames are {FRAME_LEN}")]
    WrongLength { len: usize },
    #[error("response is addressed to group {found:#04x}, not {expected:#04x}")]
    GroupMismatch { expected: u8, found: u8 },
    #[error("response is addressed to subcommand {found:#04x}, not {expected:#04x}")]
    SubcommandMismatch { expected: u8, found: u8 },
    #[error("response says {found} packets, not the {expected} that were requested")]
    PacketCountMismatch { expected: u8, found: u8 },
    #[error("response is packet {found}, not packet {expected}")]
    PacketIndexMismatch { expected: u8, found: u8 },
    #[error("response declares {len} data bytes; at most {MAX_DATA_LEN} fit in a frame")]
    DataLengthOutOfRange { len: u8 },
    #[error("aula-bytech addresses commands as a group and a subcommand, not as a bare opcode")]
    NotAGroupSubcommandPair,
}

/// The part of a response that answers the command, once the frame has been
/// checked against the command that was sent.
#[derive(Clone, PartialEq, Eq, Debug)]
pub struct ResponseData {
    /// The declared data bytes, exactly `data_len` of them.
    pub data: Vec<u8>,
    /// Whether the response's own checksum byte is what we would have computed.
    /// Recorded, never enforced -- see [`checksum_matches`].
    pub checksum_ok: bool,
}

/// Checks a response against the command it should be answering, and returns
/// its data.
///
/// The discriminator is the vendor's, and it is worth knowing how thin it is:
/// four echoed header bytes, no sequence number, no status byte. That is
/// adequate only because exactly one command is ever in flight, which is a
/// property this project keeps by sending one command at a time rather than one
/// the protocol enforces.
pub fn decode_response(
    key: CommandKey,
    request_data_len: usize,
    response: &[u8],
    report_id: u8,
) -> Result<ResponseData, ResponseError> {
    let CommandKey::GroupSubcommand { group, subcommand } = key else {
        return Err(ResponseError::NotAGroupSubcommandPair);
    };
    let frame: &[u8; FRAME_LEN] = response
        .try_into()
        .map_err(|_| ResponseError::WrongLength {
            len: response.len(),
        })?;

    if frame[GROUP] != group {
        return Err(ResponseError::GroupMismatch {
            expected: group,
            found: frame[GROUP],
        });
    }
    if frame[SUBCOMMAND] != subcommand {
        return Err(ResponseError::SubcommandMismatch {
            expected: subcommand,
            found: frame[SUBCOMMAND],
        });
    }
    if frame[TOTAL_PACKETS] != 1 {
        return Err(ResponseError::PacketCountMismatch {
            expected: 1,
            found: frame[TOTAL_PACKETS],
        });
    }
    if frame[PACKET_INDEX] != 0 {
        return Err(ResponseError::PacketIndexMismatch {
            expected: 0,
            found: frame[PACKET_INDEX],
        });
    }
    let data_len = usize::from(frame[DATA_LEN]);
    if data_len > MAX_DATA_LEN {
        return Err(ResponseError::DataLengthOutOfRange {
            len: frame[DATA_LEN],
        });
    }
    // Not an error: the request's own length byte is what tells the device how
    // much to send, so a shorter answer is a fact about the answer rather than
    // about the frame. The caller decides whether it is enough.
    let _ = request_data_len;

    Ok(ResponseData {
        data: frame[DATA..DATA + data_len].to_vec(),
        checksum_ok: checksum_matches(report_id, frame),
    })
}

/// Reports this protocol sends unprompted, which must never be mistaken for an
/// answer.
///
/// Two shapes, both from the vendor's dispatcher: three "bubble" command groups
/// that carry events, and a 19-byte dongle report. None of them can collide with
/// `0x82`, but the check is here rather than implied, because "it cannot happen"
/// is how an event ends up parsed as a model id.
pub fn is_unsolicited(report: &[u8]) -> bool {
    const BUBBLE_GROUPS: [u8; 3] = [254, 152, 148];
    if report.len() == 19 && report.first() == Some(&10) {
        return true;
    }
    report
        .first()
        .is_some_and(|group| BUBBLE_GROUPS.contains(group))
}

// --- the one command --------------------------------------------------------

/// A model identifier as this family reports it.
///
/// # Why this is not called a UUID
///
/// The vendor's method is `readDeviceUUID` and the vendor's field is `uuid`, but
/// the value is used to pick a key layout, and the ten known values across the
/// family are `SS 00 00 00 00 NN` -- a series byte, four zero bytes, and a model
/// index -- with no per-unit entropy anywhere. So it identifies a model, and the
/// type says model. Recording one is an ordinary model-identification signal
/// rather than a device serial.
///
/// If a board is ever seen answering with unit entropy, this type is wrong and
/// must split. Nothing is built so that finding that out is expensive.
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Debug)]
pub struct VendorModelId(u64);

/// The number of data bytes the model-id read asks for and expects back.
pub const MODEL_ID_LEN: usize = 6;

impl VendorModelId {
    /// Big-endian over the response's data bytes: the first byte is the most
    /// significant, which is how the vendor accumulates it.
    fn from_be_bytes(data: &[u8]) -> Self {
        Self(data.iter().fold(0u64, |acc, byte| (acc << 8) | u64::from(*byte)))
    }

    pub fn raw(self) -> u64 {
        self.0
    }

    /// The leading byte. `0x11` on every wired model in the vendor's own table
    /// and `0x12` on every wireless one -- a pattern strong enough to predict
    /// against and therefore strong enough to be wrong about.
    pub fn series(self) -> u8 {
        // Six bytes, so the series byte is bits 40..48.
        #[allow(clippy::cast_possible_truncation)]
        {
            (self.0 >> 40) as u8
        }
    }

    /// The trailing byte, which is what actually differs between models.
    #[allow(clippy::cast_possible_truncation)]
    pub fn index(self) -> u8 {
        (self.0 & 0xFF) as u8
    }

    pub fn to_be_bytes(self) -> [u8; MODEL_ID_LEN] {
        let full = self.0.to_be_bytes();
        // The low six bytes of the u64, which is the whole value: anything
        // wider than six bytes cannot be constructed from a response.
        [full[2], full[3], full[4], full[5], full[6], full[7]]
    }
}

impl std::fmt::Display for VendorModelId {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{} (0x{:012X})", self.0, self.0)
    }
}

/// Why a model-id answer was not usable.
#[derive(Clone, Copy, PartialEq, Eq, Debug, thiserror::Error)]
pub enum ModelIdError {
    #[error("frame: {0}")]
    Frame(#[from] ResponseError),
    #[error("expected {MODEL_ID_LEN} data bytes, got {len}")]
    WrongDataLength { len: usize },
}

/// The typed answer to the `aula-bytech` model-id probe.
///
/// The report id is not a parameter of this decoder even though the checksum
/// depends on it, because the checksum is recorded rather than enforced. The
/// value carries the checksum verdict; nothing in the decode path branches on
/// it.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct ModelIdProbe {
    pub model_id: VendorModelId,
    /// Whether the answer's checksum matched what we would have computed for
    /// report id 9. Informational -- the first exchange is what establishes
    /// whether the device computes it the way the request does.
    pub checksum_ok: bool,
}

impl ProbeResponse for ModelIdProbe {
    const COMMAND: ProbeCommandId = ProbeCommandId::AulaBytechReadModelId;
    type Rejection = ModelIdError;

    /// Six zero bytes. Not padding: the request's length byte is what tells the
    /// device how many bytes to answer with, and six is what the vendor asks
    /// for. A zero-length request would be a different request.
    fn request_payload() -> Vec<u8> {
        vec![0u8; MODEL_ID_LEN]
    }

    fn decode(key: CommandKey, response: &[u8]) -> Result<Self, ModelIdError> {
        // The report id used for the checksum verdict. Nine on this board, and
        // the transport derives it from the descriptor rather than trusting
        // this constant for anything that goes on the wire.
        const REPORT_ID: u8 = 9;

        let decoded = decode_response(key, MODEL_ID_LEN, response, REPORT_ID)?;
        if decoded.data.len() != MODEL_ID_LEN {
            return Err(ModelIdError::WrongDataLength {
                len: decoded.data.len(),
            });
        }
        Ok(ModelIdProbe {
            model_id: VendorModelId::from_be_bytes(&decoded.data),
            checksum_ok: decoded.checksum_ok,
        })
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::expect_used, clippy::panic, clippy::unwrap_used)]

    use super::*;

    const REPORT_ID: u8 = 9;

    fn model_id_key() -> CommandKey {
        CommandKey::GroupSubcommand {
            group: 0x82,
            subcommand: 0x01,
        }
    }

    /// The ten model ids the vendor's own HERO 84 HE module carries. Not one of
    /// them is our board's -- the vendor names none of the wired ones -- so
    /// these are decoder vectors, not an expectation about what will come back.
    const KNOWN_MODEL_IDS: [u64; 10] = [
        18_691_697_672_195,
        18_691_697_672_197,
        18_691_697_672_207,
        18_691_697_672_210,
        18_691_697_672_213,
        18_691_697_672_255,
        19_791_209_299_972,
        19_791_209_299_978,
        19_791_209_299_984,
        19_791_209_299_989,
    ];

    fn response_frame(data: &[u8]) -> [u8; FRAME_LEN] {
        let mut frame = [0u8; FRAME_LEN];
        frame[GROUP] = 0x82;
        frame[SUBCOMMAND] = 0x01;
        frame[TOTAL_PACKETS] = 1;
        frame[PACKET_INDEX] = 0;
        #[allow(clippy::cast_possible_truncation)]
        {
            frame[DATA_LEN] = data.len() as u8;
        }
        frame[DATA..DATA + data.len()].copy_from_slice(data);
        frame[CHECKSUM] = checksum(REPORT_ID, &frame);
        frame
    }

    // --- the request -----------------------------------------------------

    #[test]
    fn the_model_id_request_is_exactly_these_bytes() {
        // The vector that goes on the wire. Written out in full rather than
        // computed, so that a change to the builder has to change this line and
        // be seen in a diff.
        let frame = encode_request(model_id_key(), &ModelIdProbe::request_payload(), REPORT_ID)
            .expect("a six-byte payload fits in one frame");
        let mut expected = [0u8; FRAME_LEN];
        expected[..6].copy_from_slice(&[0x82, 0x01, 0x00, 0x01, 0x00, 0x06]);
        expected[CHECKSUM] = 0x6C;
        assert_eq!(frame, expected, "request frame changed");
    }

    #[test]
    fn the_checksum_depends_on_the_report_id() {
        // The property that makes a frame valid only for the report id it was
        // built for. If this ever passes with both values equal, the seed has
        // been dropped.
        let payload = ModelIdProbe::request_payload();
        let nine = encode_request(model_id_key(), &payload, 9).expect("valid");
        let one = encode_request(model_id_key(), &payload, 1).expect("valid");
        assert_eq!(nine[CHECKSUM], 0x6C);
        assert_eq!(one[CHECKSUM], 0x74);
        assert_ne!(nine[CHECKSUM], one[CHECKSUM]);
    }

    #[test]
    fn a_finished_frame_satisfies_the_checksum_invariant() {
        for report_id in 0u8..=255 {
            for data_len in 0..=MAX_DATA_LEN {
                let payload = vec![0xA5u8; data_len];
                let frame = encode_request(model_id_key(), &payload, report_id).expect("fits");
                let total: u32 = frame.iter().map(|b| u32::from(*b)).sum::<u32>()
                    + u32::from(report_id);
                assert_eq!(
                    total & 0xFF,
                    0xFF,
                    "invariant broken at report id {report_id}, {data_len} data bytes"
                );
            }
        }
    }

    #[test]
    fn a_payload_that_would_need_chunking_is_refused() {
        let payload = vec![0u8; MAX_DATA_LEN + 1];
        assert_eq!(
            encode_request(model_id_key(), &payload, REPORT_ID),
            Err(EncodeError::PayloadTooLong {
                len: MAX_DATA_LEN + 1
            }),
            "a second frame must never be produced silently"
        );
    }

    #[test]
    fn a_bare_opcode_cannot_be_encoded_for_this_family() {
        assert_eq!(
            encode_request(CommandKey::Opcode(0x82), &[], REPORT_ID),
            Err(EncodeError::NotAGroupSubcommandPair),
            "a group byte alone must not become a frame"
        );
    }

    // --- the response ----------------------------------------------------

    #[test]
    fn every_known_model_id_round_trips() {
        for raw in KNOWN_MODEL_IDS {
            let bytes = raw.to_be_bytes();
            let data = &bytes[2..];
            let decoded = ModelIdProbe::decode(model_id_key(), &response_frame(data))
                .expect("a well-formed answer");
            assert_eq!(decoded.model_id.raw(), raw);
            assert_eq!(decoded.model_id.to_be_bytes().as_slice(), data);
            assert!(decoded.checksum_ok);
        }
    }

    #[test]
    fn the_series_and_index_split_matches_the_vendors_own_table() {
        // Six wired models and four wireless ones, and the split is what the
        // prediction for our own board rests on.
        let wired = KNOWN_MODEL_IDS
            .iter()
            .filter(|raw| VendorModelId(**raw).series() == 0x11)
            .count();
        let wireless = KNOWN_MODEL_IDS
            .iter()
            .filter(|raw| VendorModelId(**raw).series() == 0x12)
            .count();
        assert_eq!((wired, wireless), (6, 4));
        assert_eq!(VendorModelId(19_791_209_299_989).index(), 0x15);
        assert_eq!(VendorModelId(18_691_697_672_195).index(), 0x03);
    }

    #[test]
    fn a_response_to_another_group_is_rejected() {
        let mut frame = response_frame(&[0x11, 0, 0, 0, 0, 0x05]);
        frame[GROUP] = 0x84;
        assert!(matches!(
            ModelIdProbe::decode(model_id_key(), &frame),
            Err(ModelIdError::Frame(ResponseError::GroupMismatch { .. }))
        ));
    }

    #[test]
    fn a_response_to_another_subcommand_is_rejected() {
        let mut frame = response_frame(&[0x11, 0, 0, 0, 0, 0x05]);
        frame[SUBCOMMAND] = 0x02;
        assert!(matches!(
            ModelIdProbe::decode(model_id_key(), &frame),
            Err(ModelIdError::Frame(ResponseError::SubcommandMismatch { .. }))
        ));
    }

    #[test]
    fn a_response_from_a_different_packet_sequence_is_rejected() {
        let mut wrong_count = response_frame(&[0x11, 0, 0, 0, 0, 0x05]);
        wrong_count[TOTAL_PACKETS] = 2;
        assert!(matches!(
            ModelIdProbe::decode(model_id_key(), &wrong_count),
            Err(ModelIdError::Frame(ResponseError::PacketCountMismatch { .. }))
        ));

        let mut wrong_index = response_frame(&[0x11, 0, 0, 0, 0, 0x05]);
        wrong_index[PACKET_INDEX] = 1;
        assert!(matches!(
            ModelIdProbe::decode(model_id_key(), &wrong_index),
            Err(ModelIdError::Frame(ResponseError::PacketIndexMismatch { .. }))
        ));
    }

    #[test]
    fn a_declared_length_beyond_the_frame_is_rejected() {
        let mut frame = response_frame(&[0x11, 0, 0, 0, 0, 0x05]);
        #[allow(clippy::cast_possible_truncation)]
        {
            frame[DATA_LEN] = (MAX_DATA_LEN + 1) as u8;
        }
        assert!(
            matches!(
                ModelIdProbe::decode(model_id_key(), &frame),
                Err(ModelIdError::Frame(ResponseError::DataLengthOutOfRange { .. }))
            ),
            "a length byte must never be trusted to index the frame"
        );
    }

    #[test]
    fn a_short_answer_is_not_padded_into_a_model_id() {
        let frame = response_frame(&[0x11, 0x00, 0x00]);
        assert_eq!(
            ModelIdProbe::decode(model_id_key(), &frame),
            Err(ModelIdError::WrongDataLength { len: 3 })
        );
    }

    #[test]
    fn a_frame_of_the_wrong_size_is_rejected() {
        for len in [0usize, 62, 64] {
            assert!(matches!(
                ModelIdProbe::decode(model_id_key(), &vec![0u8; len]),
                Err(ModelIdError::Frame(ResponseError::WrongLength { .. }))
            ));
        }
    }

    #[test]
    fn a_bad_checksum_is_recorded_and_not_enforced() {
        // Deliberate: the vendor never checks an incoming checksum, so whether
        // the device computes it our way is exactly what the first exchange is
        // supposed to find out. Rejecting on it would turn an informative
        // answer into a failure.
        let mut frame = response_frame(&[0x11, 0, 0, 0, 0, 0x05]);
        frame[CHECKSUM] ^= 0xFF;
        let decoded = ModelIdProbe::decode(model_id_key(), &frame).expect("still decodable");
        assert!(!decoded.checksum_ok);
    }

    // --- unsolicited traffic ---------------------------------------------

    #[test]
    fn event_reports_are_recognised_before_anything_tries_to_decode_them() {
        for group in [254u8, 152, 148] {
            let mut report = vec![0u8; FRAME_LEN];
            report[0] = group;
            assert!(is_unsolicited(&report), "group {group} was not recognised");
        }
        let mut dongle = vec![0u8; 19];
        dongle[0] = 10;
        assert!(is_unsolicited(&dongle));

        let answer = response_frame(&[0x11, 0, 0, 0, 0, 0x05]);
        assert!(!is_unsolicited(&answer), "the answer looked like an event");
    }
}
