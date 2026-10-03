"""Encodeur FIT minimal (protocole 2.0), suffisant pour les fichiers de séance.

Le SDK Python officiel de Garmin ne sait que décoder ; on écrit donc les
quelques messages nécessaires à la main (file_id, workout, workout_step pour
les séances ; record, event, lap, session, activity pour les sorties).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

PROTOCOL_VERSION = 0x20  # 2.0
PROFILE_VERSION = 21217  # 21.217, aligné sur garmin-fit-sdk

_CRC_TABLE = (
    0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
    0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400,
)


def crc16(data: bytes, crc: int = 0) -> int:
    for byte in data:
        tmp = _CRC_TABLE[crc & 0xF]
        crc = ((crc >> 4) & 0x0FFF) ^ tmp ^ _CRC_TABLE[byte & 0xF]
        tmp = _CRC_TABLE[crc & 0xF]
        crc = ((crc >> 4) & 0x0FFF) ^ tmp ^ _CRC_TABLE[(byte >> 4) & 0xF]
    return crc


@dataclass(frozen=True)
class BaseType:
    code: int
    fmt: str  # format struct, sans boutisme ; "s" pour les chaînes
    invalid: int | None


ENUM = BaseType(0x00, "B", 0xFF)
UINT8 = BaseType(0x02, "B", 0xFF)
SINT16 = BaseType(0x83, "h", 0x7FFF)
UINT16 = BaseType(0x84, "H", 0xFFFF)
UINT32 = BaseType(0x86, "I", 0xFFFFFFFF)
UINT32Z = BaseType(0x8C, "I", 0)
STRING = BaseType(0x07, "s", None)


@dataclass(frozen=True)
class FieldDef:
    num: int
    base: BaseType
    size: int = 0  # taille en octets ; obligatoire pour les chaînes

    @property
    def byte_size(self) -> int:
        return self.size or struct.calcsize(self.base.fmt)

    def encode(self, value: object) -> bytes:
        if self.base is STRING:
            raw = (str(value) if value is not None else "").encode("utf-8")
            raw = _truncate_utf8(raw, self.byte_size - 1)
            return raw.ljust(self.byte_size, b"\x00")
        if value is None:
            value = self.base.invalid
        return struct.pack("<" + self.base.fmt, int(value))


def _truncate_utf8(raw: bytes, limit: int) -> bytes:
    """Coupe sans casser un caractère multi-octets."""
    if len(raw) <= limit:
        return raw
    return raw[:limit].decode("utf-8", errors="ignore").encode("utf-8")


class FitWriter:
    """Accumule des messages puis produit un fichier FIT complet."""

    def __init__(self) -> None:
        self._records = bytearray()
        self._local: dict[tuple, int] = {}
        self._next_local = 0

    def write(self, global_num: int, fields: list[tuple[FieldDef, object]]) -> None:
        layout = (global_num, tuple((f.num, f.base.code, f.byte_size) for f, _ in fields))
        local = self._local.get(layout)
        if local is None:
            local = self._next_local
            self._next_local = (self._next_local + 1) % 16
            # Une définition locale réutilisée remplace l'ancienne.
            self._local = {k: v for k, v in self._local.items() if v != local}
            self._local[layout] = local
            self._records += struct.pack("<BBBHB", 0x40 | local, 0, 0, global_num, len(fields))
            for f, _ in fields:
                self._records += struct.pack("<BBB", f.num, f.byte_size, f.base.code)
        self._records.append(local)
        for f, value in fields:
            self._records += f.encode(value)

    def to_bytes(self) -> bytes:
        header = struct.pack(
            "<BBHI4s", 14, PROTOCOL_VERSION, PROFILE_VERSION, len(self._records), b".FIT"
        )
        header += struct.pack("<H", crc16(header))
        body = header + bytes(self._records)
        return body + struct.pack("<H", crc16(body))
