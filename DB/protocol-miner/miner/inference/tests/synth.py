"""Synthetic packet corpora for engine unit tests.

Nothing here comes from AULA.  Each corpus is built to exhibit exactly one
structural artifact so that a single engine defect can be reproduced in
isolation, with the right answer known by construction rather than looked up in
a ground truth file.
"""
from __future__ import annotations

WIDTH = 63


def frame(op, sub, body, *, at_sub=1, at_len=5, at_body=6, extra=None,
          checksum=False):
    """A frame in the generic two-level shape: op, sub, len, body.

    The trailing check byte is off by default.  A checksum is a perfect
    discriminator of every other byte in the frame, which would drown out the
    structure each corpus is built to isolate; the framing tests are not about
    the check byte.
    """
    b = [0] * WIDTH
    b[0] = op
    b[at_sub] = sub
    b[at_len] = len(body)
    for i, v in enumerate(body):
        b[at_body + i] = v & 0xFF
    for off, val in (extra or {}).items():
        b[off] = val & 0xFF
    if checksum:
        b[WIDTH - 1] = (246 - sum(b[0:WIDTH - 1])) % 256
    return b


def rows(frames):
    return [{"id": i, "payload_hex": bytes(f).hex(), "session": "s", "seq": i,
             "source": "SYNTH"} for i, f in enumerate(frames)]


# --------------------------------------------------------------------------
# C-1: an offset that shatters a command group into singletons
# --------------------------------------------------------------------------

def corpus_singleton_shatter():
    """Two commands, both carrying their real second-level id at offset 1.

    Offset 9 carries a per-frame serial number, so partitioning on it produces
    nothing but groups of one -- inside which every other byte is trivially
    constant.  An engine without a singleton guard scores offset 9 higher than
    the real one and picks it.

    The right answer is 1, and it is right for a reason a guard can see: the
    groups at offset 1 have more than one member and are still internally
    constant.
    """
    frames = []
    serial = 0
    for op in (0x40, 0x41):
        for sub in (1, 2, 3):
            for _ in range(4):
                serial += 1
                frames.append(frame(op, sub, [sub * 10, 0], extra={9: serial}))
    return rows(frames)


# --------------------------------------------------------------------------
# C-2: a minority of opcodes carrying the majority of the evidence
# --------------------------------------------------------------------------

def corpus_evidence_volume():
    """Five thin commands vote for offset 3; two fat ones vote for offset 1.

    Counted one vote per opcode the thin majority wins 5-2.  Counted by how
    many frames actually support each reading, offset 1 wins 480 to 30.  The
    right answer is 1.
    """
    frames = []
    # Fat commands: second-level id at offset 1, offset 3 held constant.
    for op in (0x82, 0x84):
        for sub in range(6):
            for k in range(40):
                frames.append(frame(op, sub, [sub, 0], extra={3: 1, 40: k % 2}))
    # Thin commands: second-level id at offset 3, offset 1 held constant.
    for op in (0x93, 0x95, 0x96, 0x99, 0x9A):
        for sub in range(3):
            for _ in range(2):
                frames.append(frame(op, 0, [7, 7], extra={3: sub}))
    return rows(frames)


# --------------------------------------------------------------------------
# stride: bodies whose length constrains the record size only up to a divisor
# --------------------------------------------------------------------------

def corpus_records():
    """Three record commands with different divisor lattices.

    0x50  bodies of 5 and 55   -> gcd 5, prime: the stride is pinned
    0x51  bodies of 8 only     -> gcd 8: strides 2, 4 and 8 all fit the lengths
    0x52  bodies of 14 and 22  -> gcd 2, but no body as short as one record
    """
    frames = []
    for rep in range(6):
        frames.append(frame(0x50, 0, [rep, 1, 2, 3, 4]))
        frames.append(frame(0x50, 0, [(rep + i) % 7 for i in range(55)]))
        frames.append(frame(0x51, 0, [rep, 0, 1, 2, 3, 4, 5, 6]))
        frames.append(frame(0x52, 0, [(rep + i) % 5 for i in range(14)]))
        frames.append(frame(0x52, 0, [(rep + i) % 5 for i in range(22)]))
    return rows(frames)
