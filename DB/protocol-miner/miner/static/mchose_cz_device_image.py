"""A synthetic CZ device image assembled from the vendor's own shipped data.

`synthetic_from_vendor_schema`, in the strictest available sense: not "bytes
that look plausible" but **the vendor's own default key table for this exact
product**, re-serialised in the vendor's own wire order. Nothing here is
invented, and nothing here is evidence about hardware. It exists so the real
configurator will render, so its UI can be walked, so the frames it emits are
the vendor's frames rather than our guesses.

## The chain, all quoted from `CZ_SHARED_DATA/main.<hash>.js`

```js
getInfo()               -> _simpleGetCommand(3, 0, 56)
getBase()               -> _simpleGetCommand(4, 0, 56)
getFuncConfig(profile)  -> _simpleGetCommand(5, 64 * profile, 64)
getKeyMatrix(t, true)   -> _simpleGetCommand(7, totalKeyAreaSize * t, usedKeyAreaSize)
totalKeyAreaSize = 512
usedKeyAreaSize  = 3 * maxKeyCount

getDefaultKeyInfos(layer):
  n = await channel.getKeyMatrix(layer, true)
  for (i = 0; i < maxKeyCount; i++) [type, code1, code2] = n.slice(3i, 3i+3)

initDefaultKeys:
  l = maxKeyboardProfileLength ?? (await getBase()).maxKeyboardProfileLength
  for (d = 0; d < MAX_LAYER * l; d++) u[d] = await getDefaultKeyInfos(d)
```

So layer/profile slot `t` lives at `512 * t` and is 3 bytes per key. The shipped
`default-keys-god-60.js` is a list of 128 `{type, code1, code2, code, name,
index, layer}` records — the same fields, in the same order, for the same
product. `3 * 128 = 384 = 0x180`, which is exactly the region size measured from
56 captured frames across 8 regions at stride `0x200`. The measurement and the
source agree, which is why this is worth doing rather than guessing.

## What this is not

It is not a device. Every byte served from here is stamped
`synthetic_from_vendor_schema` at the point of use, the echo audit classifies it
as non-evidence, and no parameter value "learned" from a reply built here may be
reported as observed. Its only claim is: the vendor's client accepts it.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

TOTAL_KEY_AREA_SIZE = 512
BYTES_PER_KEY = 3

# The vendor's own key-code derivation, transcribed from `getKeyCode` in
# CZ_SHARED_DATA/main.<hash>.js:
#
#     if (16 === type) { a = code2; if (code1) a = {1:224,2:225,4:226,8:227,
#                                                   16:228,32:229,64:230,128:231}[code1] }
#     else if (240 == type && 255 == code1) a = 255;
#     else if (240 == type &&  30 == code1) a = 251;
#     else if (240 == type && 250 == code1) a = 252;
#     else if (240 == type &&  52 == code1) a = 253;
#     else if (48  == type && 226 == code1) a = 3809;
#     else if (128 == type &&  66 == code1) a = 3810;
#     return a;
SPECIAL_CODE_TO_TRIPLE: dict[int, tuple[int, int, int]] = {
    255: (240, 255, 0),
    251: (240, 30, 0),
    252: (240, 250, 0),
    253: (240, 52, 0),
    3809: (48, 226, 0),
    3810: (128, 66, 0),
}
_MODIFIER_CODE1 = {224: 1, 225: 2, 226: 4, 227: 8, 228: 16, 229: 32, 230: 64, 231: 128}


def key_code(type_: int, code1: int, code2: int) -> int:
    """`getKeyCode`, transcribed. Used to CHECK the inverse table, not to guess."""
    if type_ == 16:
        if code1 == 0:
            return code2
        return {1: 224, 2: 225, 4: 226, 8: 227,
                16: 228, 32: 229, 64: 230, 128: 231}.get(code1, 0)
    if type_ == 240 and code1 == 255:
        return 255
    if type_ == 240 and code1 == 30:
        return 251
    if type_ == 240 and code1 == 250:
        return 252
    if type_ == 240 and code1 == 52:
        return 253
    if type_ == 48 and code1 == 226:
        return 3809
    if type_ == 128 and code1 == 66:
        return 3810
    return 0


def triple_for_code(code: int) -> tuple[int, int, int]:
    """The (type, code1, code2) the vendor's own mapping sends to `code`."""
    if code in SPECIAL_CODE_TO_TRIPLE:
        return SPECIAL_CODE_TO_TRIPLE[code]
    if code in _MODIFIER_CODE1:
        return (16, _MODIFIER_CODE1[code], 0)
    if 0 < code < 224:
        return (16, 0, code)
    raise ValueError(f"no vendor-stated triple produces code {code}")

# `{type:16,code1:0,code2:20,code:20,name:"Q",index:0,layer:0}` -- read only the
# three fields that reach the wire. `code` and `name` are derived by the client
# from those three, so taking them from the file would be inventing agreement.
_KEY = re.compile(
    r"\{type:(\d+),code1:(\d+),code2:(\d+),code:(-?\d+),name:\"((?:[^\"\\]|\\.)*)\",index:(\d+),layer:(\d+)\}"
)


@dataclass(frozen=True)
class DefaultKeys:
    source_file: str
    keys: tuple[tuple[int, int, int], ...]   # (type, code1, code2) by index

    @property
    def key_count(self) -> int:
        return len(self.keys)

    @property
    def used_key_area_size(self) -> int:
        return BYTES_PER_KEY * self.key_count

    def to_wire(self) -> bytes:
        out = bytearray()
        for t, c1, c2 in self.keys:
            out += bytes((t & 0xFF, c1 & 0xFF, c2 & 0xFF))
        return bytes(out)


def parse_default_keys(path: Path) -> DefaultKeys:
    """Read a shipped `default-keys-*.js` into wire-order triples."""
    text = path.read_text(encoding="utf-8", errors="replace")
    found = _KEY.findall(text)
    if not found:
        raise ValueError(f"{path.name} contains no key records in the expected shape")
    by_index: dict[int, tuple[int, int, int]] = {}
    for typ, c1, c2, _code, _name, index, _layer in found:
        by_index[int(index)] = (int(typ), int(c1), int(c2))
    n = max(by_index) + 1
    missing = [i for i in range(n) if i not in by_index]
    if missing:
        raise ValueError(
            f"{path.name} is missing key indices {missing[:8]}; a gap would be silently "
            "padded with zeros and become a key the vendor never shipped"
        )
    return DefaultKeys(source_file=path.name, keys=tuple(by_index[i] for i in range(n)))


class DeviceImage:
    """Per-command address spaces, served by offset.

    Each CZ command addresses its own space (`getInfo` at offset 0 of command 3,
    `getKeyMatrix` at `512*t` of command 7), so images are keyed by command
    rather than pooled. Unset bytes read as zero, which is what the harness
    already served and what the client already tolerated.
    """

    def __init__(self) -> None:
        self.spaces: dict[int, bytearray] = {}
        self.provenance: dict[int, str] = {}

    def write(self, command: int, offset: int, data: bytes, provenance: str) -> None:
        space = self.spaces.setdefault(command, bytearray())
        end = offset + len(data)
        if len(space) < end:
            space.extend(b"\x00" * (end - len(space)))
        space[offset:end] = data
        self.provenance[command] = provenance

    def read(self, command: int, offset: int, size: int) -> bytes | None:
        """The bytes for this read, or None if nothing was ever written here.

        None means "this harness has nothing to say", and the caller falls back
        to zeros. Returning zeros here instead would erase the difference
        between a deliberate value and an absent one.
        """
        space = self.spaces.get(command)
        if space is None:
            return None
        window = bytes(space[offset:offset + size])
        if len(window) < size:
            window += b"\x00" * (size - len(window))
        return window

    def describe(self) -> dict:
        return {
            "commands": {
                f"{c:#04x}": {"bytes": len(s), "provenance": self.provenance.get(c)}
                for c, s in sorted(self.spaces.items())
            }
        }


def patch_required_codes(dk: DefaultKeys, required: list[int]) -> tuple[DefaultKeys, list[dict]]:
    """Ensure every required key code exists, using the vendor's inverse mapping.

    Why this is needed at all: the God 60 layout the vendor ships contains

        index: e => { let {defaultKeyDict: i} = e; return i[252].index + 1 }

    so if no layer-0 key derives to code 252 the configurator throws
    `Cannot read properties of undefined (reading 'index')` and renders nothing.
    The shipped `default-keys-god-60.js` has no such key. Every other code the
    layout references resolves through `b[code]?.index ?? -1` and merely goes
    unplaced, so 252 is the only one that must be present.

    Two parts, with different standing, and they are kept apart on purpose:

    * WHICH bytes produce code 252 is the vendor's own `getKeyCode` mapping,
      read from its source: `type=240, code1=250`.
    * WHERE that key sits is a HARNESS CHOICE. The first unused slot is used
      (the shipped table's `type:0` placeholders), and the choice is returned so
      it lands in the manifest instead of disappearing into a byte array.
    """
    keys = list(dk.keys)
    present = {key_code(*k) for k in keys}
    patches: list[dict] = []
    for code in required:
        if code in present:
            continue
        triple = triple_for_code(code)
        slot = next((i for i, k in enumerate(keys) if k[0] in (0, 255)), None)
        if slot is None:
            raise ValueError(f"no unused slot to host code {code}")
        patches.append({
            "code": code,
            "triple": list(triple),
            "slot_index": slot,
            "replaced": list(keys[slot]),
            "triple_provenance": "vendor getKeyCode mapping in CZ_SHARED_DATA/main.js",
            "slot_provenance": "HARNESS CHOICE: first placeholder slot; the vendor states no index",
            "why_required": "the shipped God60 layout dereferences defaultKeyDict[252].index",
        })
        keys[slot] = triple
        present.add(code)
    return DefaultKeys(source_file=dk.source_file, keys=tuple(keys)), patches


def build_god60_image(default_keys_path: Path, layer_slots: int = 8,
                      required_codes: list[int] | None = None) -> tuple[DeviceImage, dict]:
    """The minimum that makes the configurator render, and nothing more.

    `layer_slots` is `MAX_LAYER * maxKeyboardProfileLength`. With an all-zero
    `getBase` response the vendor's own parser yields
    `maxKeyboardProfileLength = 2` (its `supportMultiProfile && !supportOrderProfile`
    branch), and `MAX_LAYER` is 4, so the client asks for slots 0..7 -- which is
    exactly the 8 regions observed in the capture. Every slot is filled with the
    same shipped layer-0 table: the file ships one layer, and duplicating it is
    visible in the manifest rather than hidden.
    """
    dk = parse_default_keys(default_keys_path)
    dk, patches = patch_required_codes(dk, required_codes if required_codes is not None else [252])
    img = DeviceImage()
    wire = dk.to_wire()
    for slot in range(layer_slots):
        img.write(7, TOTAL_KEY_AREA_SIZE * slot, wire,
                  f"{dk.source_file} layer-0 table, re-serialised as [type,code1,code2]")
    manifest = {
        "_what": "synthetic CZ device image, TICKET-25",
        "evidence_class": "synthetic_from_vendor_schema",
        "not_hardware_evidence": (
            "These bytes are the vendor's own shipped defaults, not a device's answer. "
            "No parameter value read back from them is an observation."
        ),
        "source_file": dk.source_file,
        "key_count": dk.key_count,
        "bytes_per_key": BYTES_PER_KEY,
        "used_key_area_size": dk.used_key_area_size,
        "total_key_area_size": TOTAL_KEY_AREA_SIZE,
        "layer_slots_filled": layer_slots,
        "layer_slots_note": (
            "the shipped file contains layer 0 only; the same table is served for every "
            "slot, which is a harness choice and is recorded as one"
        ),
        "commands_backed": img.describe()["commands"],
        "required_code_patches": patches,
        "commands_left_as_zeros": [
            "0x03 getInfo", "0x04 getBase", "0x05 getFuncConfig", "0x0c", "0x0d", "0xa9",
            "0xf1", "0xf2",
        ],
    }
    return img, manifest


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--default-keys", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    img, manifest = build_god60_image(Path(args.default_keys))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"keys           : {manifest['key_count']}")
    print(f"region size    : {manifest['used_key_area_size']} bytes")
    print(f"slots filled   : {manifest['layer_slots_filled']}")
    print(f"-> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
