"""Minimal, bounds-checked PE import-table reader; no code execution or disassembly."""

from __future__ import annotations

import struct


def imports(raw: bytes) -> list[str]:
    if len(raw) < 0x40 or raw[:2] != b"MZ":
        return []
    pe_offset = struct.unpack_from("<I", raw, 0x3C)[0]
    if pe_offset + 24 > len(raw) or raw[pe_offset:pe_offset + 4] != b"PE\0\0":
        return []
    section_count = struct.unpack_from("<H", raw, pe_offset + 6)[0]
    optional_size = struct.unpack_from("<H", raw, pe_offset + 20)[0]
    optional = pe_offset + 24
    if optional + optional_size > len(raw) or optional_size < 104:
        return []
    magic = struct.unpack_from("<H", raw, optional)[0]
    data_directory = optional + (96 if magic == 0x10B else 112 if magic == 0x20B else -1)
    if data_directory < optional or data_directory + 16 > optional + optional_size:
        return []
    import_rva, import_size = struct.unpack_from("<II", raw, data_directory + 8)
    if not import_rva or not import_size:
        return []
    sections: list[tuple[int, int, int]] = []
    section_offset = optional + optional_size
    for index in range(section_count):
        start = section_offset + index * 40
        if start + 40 > len(raw):
            return []
        virtual_size, virtual_address, raw_size, raw_offset = struct.unpack_from("<IIII", raw, start + 8)
        sections.append((virtual_address, max(virtual_size, raw_size), raw_offset))

    def rva_offset(rva: int) -> int | None:
        for address, size, offset in sections:
            if address <= rva < address + size:
                position = offset + rva - address
                return position if position < len(raw) else None
        return None

    table = rva_offset(import_rva)
    if table is None:
        return []
    result: list[str] = []
    for offset in range(table, min(table + import_size, len(raw) - 20) + 1, 20):
        original_thunk, _, _, name_rva, first_thunk = struct.unpack_from("<IIIII", raw, offset)
        if not any((original_thunk, name_rva, first_thunk)):
            break
        name_offset = rva_offset(name_rva)
        if name_offset is None:
            continue
        end = raw.find(b"\0", name_offset, min(name_offset + 260, len(raw)))
        if end == -1:
            continue
        try:
            result.append(raw[name_offset:end].decode("ascii").lower())
        except UnicodeDecodeError:
            continue
    return sorted(set(result))
