"""Общие тестовые вспомогательные средства: сборка минимального DBF в памяти.

Боевые/тестовые .dbf в репозиторий не кладём — фикстуры строятся на лету.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest


def build_dbf(path: Path, fields, rows, *, ldid: int = 0xC9, deleted=None) -> Path:
    """Пишет простой DBF (все поля типа C) для тестов.

    fields   — список (имя, длина);
    rows     — список кортежей строковых значений (по числу полей);
    ldid     — язык-драйвер (0xC9 = cp1251);
    deleted  — необязательный список bool: пометить запись флагом удаления '*'.
    """
    path = Path(path)
    header_len = 32 + 32 * len(fields) + 1
    record_len = 1 + sum(length for _, length in fields)
    hdr = bytearray(header_len)
    hdr[0] = 0x03  # dBASE III
    struct.pack_into("<I", hdr, 4, len(rows))
    struct.pack_into("<H", hdr, 8, header_len)
    struct.pack_into("<H", hdr, 10, record_len)
    hdr[29] = ldid
    pos = 32
    for name, length in fields:
        fd = bytearray(32)
        nb = name.encode("ascii")[:11]
        fd[: len(nb)] = nb
        fd[11] = ord("C")
        fd[16] = length
        hdr[pos : pos + 32] = fd
        pos += 32
    hdr[pos] = 0x0D  # терминатор описателей полей

    body = bytearray()
    for i, row in enumerate(rows):
        body += b"*" if (deleted and deleted[i]) else b" "
        for (_name, length), val in zip(fields, row):
            body += str(val).encode("cp1251")[:length].ljust(length)
    path.write_bytes(bytes(hdr) + bytes(body) + b"\x1a")
    return path


# стандартный набор полей реестра для тестов
REGISTRY_FIELDS = [
    ("KOD_TALON", 10), ("SURNAME", 12), ("NAME", 10),
    ("KOTD", 2), ("KMKB", 6), ("STOIM", 10), ("ISHOD", 2),
]


@pytest.fixture
def make_dbf():
    return build_dbf


@pytest.fixture
def registry_fields():
    return list(REGISTRY_FIELDS)
