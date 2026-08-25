"""The MCHOSE **CZ** transport envelope, transcribed from the vendor's own SDK.

Source: `cizhou/CZ_SHARED_DATA/main.<hash>.js` (see
`reports/protocol_knowledge/mchose/acquisition/cz_sdk_manifest.json`). This is a
FIFTH code base, separate from the M HUB bundle and from the four transports the
static lane already keeps apart. Nothing here is shared with the BY keyboard
family (`hpe` / `navigator.deviceHandler`), with the mouse receiver, with the
AA-55 OTA path on report id 77, or with AULA. It is a distinct family and stays
one until data says otherwise.

## The builder, verbatim

    var u = hJ(c.offset), f = u[0], p = u[1],
        h = isArray(s.data)
              ? [s.data.length, f, p, 0].concat(s.data.map(e => 255 & e))
              : [s.data.size, f, p, 0];
    c.data = [c.flag, c.command, 0, 255 & sum(h)].concat(h);
    assert(c.data.length <= 64, "数据包长度不能超过 64");

with

    hJ = e => [255 & e, e >> 8 & 255];          // offset, 16-bit little-endian
    sum = lodash `sum` (webpack module 36119: baseSum(r, identity))
    CmdQueueItem.NORMAL_OPTIONS = { flag: 85, expect: 170, offset: 0 };

giving

    [0] flag       0x55 request / 0xAA expected in the reply
    [1] command
    [2] 0          constant in the builder
    [3] sum(bytes 4..) & 0xFF
    [4] size: bytes supplied (write) OR bytes requested (read) -- see CzFrame
    [5] offset & 0xFF
    [6] (offset >> 8) & 0xFF
    [7] 0
    [8:] payload for a write, packet padding for a read; the frame does not say which

## The reader, verbatim

    n = new Uint8Array(t.data.buffer.slice(0, 8));
    r = n[0] === this.expect && n[1] === this.command
        && n[4] <= (this.data[4] || 0)
        && n[5] === (this.data[5] || 0)
        && n[6] === (this.data[6] || 0);

and the payload is `Array.from(new Uint8Array(t.data.buffer)).slice(8)`.

## What is proven and what is not

The layout is **checked against a captured frame**, not merely transcribed:
`55 03 00 38 38 00 00 00` + 56 zero bytes decodes to command 3, size 56,
offset 0, checksum 56 — and 56 is exactly `sum([56, 0, 0, 0] + 56 zeros)`. The
frame reproduces byte-for-byte.

The checksum being a SUM rather than an XOR is **not** established by that frame
(over an all-zero payload the two agree). It is established by reading the
lodash module the SDK imports. `checksum_is_sum_not_xor_provenance()` states
which of the two claims rests on which evidence, because a captured frame that
cannot discriminate must not be cited as though it could.

The MEANING of any command byte is NOT established here. This module encodes and
decodes an envelope; it does not know what command 3 asks for.
"""
from __future__ import annotations

from dataclasses import dataclass, field

FLAG_REQUEST = 0x55
FLAG_EXPECT_REPLY = 0xAA
HEADER_LENGTH = 8
MAX_PACKET = 64

PROVENANCE = (
    "cizhou/CZ_SHARED_DATA/main.d9b73400da4734c3e3c2.js — CmdQueueItem builder "
    "and _isMatch; NORMAL_OPTIONS {flag:85, expect:170, offset:0}"
)


def checksum(body: list[int]) -> int:
    """`255 & sum(h)` where h is the frame from byte 4 onward."""
    return sum(body) & 0xFF


def split_offset(offset: int) -> tuple[int, int]:
    """`hJ = e => [255 & e, e >> 8 & 255]` — 16-bit little-endian."""
    return offset & 0xFF, (offset >> 8) & 0xFF


@dataclass
class CzFrame:
    """A decoded CZ frame.

    `size` is byte 4. It is deliberately NOT called "payload length", because the
    vendor uses the same field for two different things:

        _simpleGetCommand(cmd, off, total)   ->  data: {size: N}   # a READ of N bytes
        _simpleFullSendCommand(cmd, off, d)  ->  data: [...]       # a WRITE of len(d)

    Both put N into byte 4. A read leaves bytes 8.. as packet padding; a write
    puts its payload there. **The frame does not say which.** A write whose
    payload happens to be all zeros is byte-identical to a read of the same size
    at the same offset, so `direction` is UNKNOWN from bytes alone and this class
    refuses to guess it. That ambiguity is the same shape as the BY wired-0x04
    one and belongs in the safety classification, not in a comment.
    """

    flag: int
    command: int
    reserved2: int
    checksum: int
    size: int
    offset: int
    reserved7: int
    trailing: list[int] = field(default_factory=list)

    @property
    def checksum_ok(self) -> bool:
        lo, hi = split_offset(self.offset)
        return self.checksum == checksum([self.size, lo, hi, self.reserved7] + self.trailing)

    @property
    def direction(self) -> str:
        """READ or WRITE cannot be read off a CZ frame. Say so, every time."""
        return "UNKNOWN"

    @property
    def trailing_is_all_zero(self) -> bool:
        return all(b == 0 for b in self.trailing)

    def as_dict(self) -> dict:
        return {
            "flag": self.flag,
            "flag_role": ("request" if self.flag == FLAG_REQUEST
                          else "reply" if self.flag == FLAG_EXPECT_REPLY else "unknown"),
            "command": self.command,
            "checksum": self.checksum,
            "checksum_ok": self.checksum_ok,
            "size": self.size,
            "offset": self.offset,
            "direction": self.direction,
            "direction_note": (
                "byte 4 carries 'bytes requested' for a read and 'bytes supplied' for a "
                "write; the frame does not distinguish them"
            ),
            "trailing_hex": bytes(self.trailing).hex(),
            "trailing_is_all_zero": self.trailing_is_all_zero,
        }


def build(command: int, data: list[int] | None = None, offset: int = 0,
          flag: int = FLAG_REQUEST, packet_length: int = MAX_PACKET,
          read_size: int | None = None) -> bytes:
    """Build a CZ frame exactly as the vendor's CmdQueueItem constructor does.

    `read_size` builds the vendor's read form (`data: {size: N}`): byte 4 carries
    N and no payload follows, so the rest of the packet is padding.
    """
    if read_size is not None:
        if data:
            raise ValueError("a read frame carries a size, not a payload")
        lo, hi = split_offset(offset)
        body = [read_size & 0xFF, lo, hi, 0]
        frame = [flag & 0xFF, command & 0xFF, 0, checksum(body)] + body
        return bytes(frame) + bytes(max(0, packet_length - len(frame)))
    payload = [b & 0xFF for b in (data or [])]
    lo, hi = split_offset(offset)
    body = [len(payload), lo, hi, 0] + payload
    frame = [flag & 0xFF, command & 0xFF, 0, checksum(body)] + body
    if len(frame) > MAX_PACKET:
        raise ValueError(f"CZ frame is {len(frame)} bytes; the vendor asserts <= {MAX_PACKET}")
    return bytes(frame) + bytes(max(0, packet_length - len(frame)))


def parse(raw: bytes) -> CzFrame:
    if len(raw) < HEADER_LENGTH:
        raise ValueError(f"CZ frame needs at least {HEADER_LENGTH} bytes, got {len(raw)}")
    size = raw[4]
    # `trailing`, not `data`: for a read these bytes are packet padding, for a
    # write they are the payload, and the frame does not say which. Naming them
    # "data" would silently decide that question -- the same way a length-bounded
    # comparison on AULA made an empty payload trivially "match".
    trailing = list(raw[HEADER_LENGTH:HEADER_LENGTH + size])
    return CzFrame(
        flag=raw[0], command=raw[1], reserved2=raw[2], checksum=raw[3],
        size=size, offset=raw[5] | (raw[6] << 8), reserved7=raw[7], trailing=trailing,
    )


def reply_matches_request(request: bytes, reply: bytes) -> bool:
    """The vendor's `_isMatch`, transcribed. Nothing added, nothing relaxed."""
    if len(request) < HEADER_LENGTH or len(reply) < HEADER_LENGTH:
        return False
    return (reply[0] == FLAG_EXPECT_REPLY
            and reply[1] == request[1]
            and reply[4] <= request[4]
            and reply[5] == request[5]
            and reply[6] == request[6])


def synthesize_reply(request: bytes, payload: list[int] | None = None,
                     packet_length: int = MAX_PACKET) -> bytes:
    """A reply the vendor's own `_isMatch` accepts, built from its own schema.

    **This is `synthetic_from_vendor_schema`, not hardware evidence.** It proves
    only that the harness can satisfy the client's matcher. Any parameter whose
    value is "learned" from a reply built here is learned from us, and the echo
    audit must classify it as such.

    The payload defaults to zeros of the requested length, because inventing
    plausible-looking content is how a synthetic reply gets mistaken later for
    an observation.
    """
    req = parse(request)
    data = [b & 0xFF for b in (payload if payload is not None else [0] * req.size)]
    if len(data) > req.size:
        raise ValueError(
            f"reply payload {len(data)} exceeds the requested {req.size}; "
            "the vendor's _isMatch requires reply[4] <= request[4]"
        )
    return build(req.command, data, offset=req.offset,
                 flag=FLAG_EXPECT_REPLY, packet_length=packet_length)


def checksum_is_sum_not_xor_provenance() -> dict:
    """Which claim rests on which evidence. Kept as data so it can be asserted."""
    return {
        "layout": {
            "claim": "8-byte header [flag, command, 0, checksum, len, off_lo, off_hi, 0]",
            "evidence": "vendor builder source AND byte-for-byte reproduction of a captured frame",
            "captured_frame": "5503003838" + "00" * 59,
        },
        "checksum_is_sum": {
            "claim": "checksum is an arithmetic sum & 0xFF, not an XOR",
            "evidence": "vendor imports lodash `sum` (webpack module 36119: baseSum(r, identity))",
            "NOT_evidence": (
                "the captured frame does not discriminate: its payload is all zeros, "
                "so sum and xor agree on it"
            ),
        },
        "command_semantics": {
            "claim": None,
            "evidence": "none — this module decodes an envelope and does not know what any command means",
        },
        "direction": {
            "claim": None,
            "evidence": (
                "none — byte 4 is 'bytes requested' in _simpleGetCommand and 'bytes "
                "supplied' in _simpleFullSendCommand, and an all-zero write is "
                "byte-identical to a read of the same size at the same offset"
            ),
        },
    }
