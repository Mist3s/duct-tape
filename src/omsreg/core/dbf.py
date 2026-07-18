"""Единый минимальный читатель/писатель DBF (dBASE III/IV, FoxPro, Visual FoxPro).

Раньше этот класс был скопирован в три модуля с двумя несовместимыми API
(в одних — список кортежей полей и доступ по дескриптору, в другом — словарь полей
и доступ по имени). Здесь они объединены: value() принимает и имя поля, и дескриптор
DbfField, а записи хранятся списком (records) и одновременно доступны по индексу
(record(i)) — так подходят оба прежних стиля вызова.

Запись (save) сделана безопасной для боевых данных:
  * атомарно — сначала во временный файл рядом, fsync, затем os.replace, поэтому
    исходный файл никогда не обрезается «на полуслове» при сбое/переполнении диска;
  * при упаковке (физическом удалении записей) можно сбросить флаг структурного
    индекса в заголовке (байт 28, бит 0x01), чтобы потребитель (FoxPro) не открыл
    устаревший .cdx — сами файлы-спутники обрабатываются в omsreg.core.backup.
"""

from __future__ import annotations

import os
import struct
import tempfile
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

TALON_FIELD_DEFAULT = "KOD_TALON"

# язык-драйвер DBF (байт 29) -> кодировка содержимого DBF (для показа ФИО и данных)
LDID_CODEPAGES = {
    0x65: "cp866",
    0x26: "cp866",
    0x01: "cp437",
    0xC9: "cp1251",
    0x57: "cp1252",
    0x03: "cp1252",
}

# флаг в байте 28 заголовка: наличие структурного .cdx-индекса
FLAG_HAS_STRUCTURAL_INDEX = 0x01


class DbfField(NamedTuple):
    name: str
    type: str
    offset: int  # смещение значения внутри записи (после байта-флага удаления)
    length: int


class DbfTable:
    """Читает DBF целиком в память. Заголовок сохраняется байт-в-байт (кроме числа
    записей, даты изменения и, по запросу, флага индекса)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        data = self.path.read_bytes()
        if len(data) < 33:
            raise ValueError(f"{self.path.name}: файл слишком мал для DBF")
        self.version = data[0]
        self.nrec, self.header_len, self.record_len = struct.unpack("<IHH", data[4:12])
        if self.header_len < 33 or self.header_len > len(data) or self.record_len < 1:
            raise ValueError(f"{self.path.name}: некорректный заголовок DBF")
        self.ldid = data[29]
        self.codepage = LDID_CODEPAGES.get(self.ldid, "cp866")
        self.header = data[: self.header_len]

        # описатели полей: по 32 байта, начиная с 32-го, до байта-терминатора 0x0D
        self.fields: list[DbfField] = []
        self._by_name: dict[str, DbfField] = {}
        pos, off = 32, 1  # первый байт записи — флаг удаления
        while pos + 32 <= self.header_len and data[pos] != 0x0D:
            fd = data[pos : pos + 32]
            name = fd[:11].split(b"\x00")[0].decode("ascii", "replace").strip()
            fld = DbfField(name, chr(fd[11]), off, fd[16])
            self.fields.append(fld)
            self._by_name[name.upper()] = fld
            off += fd[16]
            pos += 32
        if off != self.record_len:
            raise ValueError(
                f"{self.path.name}: сумма длин полей ({off}) не совпадает с длиной записи "
                f"({self.record_len}) — обработка прервана, чтобы не повредить файл"
            )

        body_need = self.nrec * self.record_len
        body_have = len(data) - self.header_len
        if body_have < body_need:
            raise ValueError(
                f"{self.path.name}: файл короче, чем заявлено в заголовке "
                f"(записей {self.nrec} x {self.record_len} байт, "
                f"не хватает {body_need - body_have} байт)"
            )
        self.records: list[bytes] = [
            data[self.header_len + i * self.record_len : self.header_len + (i + 1) * self.record_len]
            for i in range(self.nrec)
        ]
        # обычно b'\x1a' (маркер конца файла) или пусто
        self.trailing = data[self.header_len + body_need :]

    # --- совместимость с прежними компактными обращениями ---
    def __len__(self) -> int:
        return self.nrec

    def record(self, i: int) -> bytes:
        return self.records[i]

    # --- доступ к полям ---
    def field(self, name: str) -> DbfField | None:
        """Дескриптор поля по имени (без учёта регистра) или None."""
        return self._by_name.get(name.upper())

    def has_field(self, name: str) -> bool:
        return name.upper() in self._by_name

    def _resolve(self, field: str | DbfField) -> DbfField:
        if isinstance(field, DbfField):
            return field
        fld = self.field(field)
        if fld is None:
            raise KeyError(f"{self.path.name}: нет поля {field}")
        return fld

    def value(self, record: bytes, field: str | DbfField) -> str:
        """Строковое значение поля (по имени или дескриптору), без хвостовых пробелов."""
        fld = self._resolve(field)
        raw = record[fld.offset : fld.offset + fld.length]
        return raw.replace(b"\x00", b" ").decode(self.codepage, "replace").strip()

    def int_value(self, record: bytes, field: str | DbfField) -> int | None:
        from omsreg.core.convert import as_int

        return as_int(self.value(record, field))

    def float_value(self, record: bytes, field: str | DbfField) -> float | None:
        from omsreg.core.convert import as_float

        return as_float(self.value(record, field))

    def code_value(self, record: bytes, field: str | DbfField) -> int | None:
        from omsreg.core.convert import as_code

        return as_code(self.value(record, field))

    def is_deleted(self, record: bytes) -> bool:
        """True, если запись помечена флагом удаления DBF ('*')."""
        return record[0:1] == b"*"

    # --- запись ---
    def save(
        self,
        records: Iterable[bytes],
        out_path: Path,
        *,
        clear_structural_index: bool = False,
        update_date: bool = True,
    ) -> None:
        """Атомарно записывает заголовок + записи + маркер конца файла (0x1A).

        Пишется во временный файл в той же папке, сбрасывается на диск (fsync) и
        переименовывается поверх целевого (os.replace) — исходный файл не может
        остаться обрезанным при сбое записи.

        clear_structural_index — сбросить бит наличия .cdx в заголовке (нужно при
        упаковке, когда устаревший индекс будет удалён отдельно).
        update_date — обновить дату последнего изменения в заголовке (байты 1-3).
        """
        out_path = Path(out_path)
        records = list(records)
        hdr = bytearray(self.header)
        struct.pack_into("<I", hdr, 4, len(records))
        if update_date:
            now = datetime.now()
            hdr[1] = now.year % 100
            hdr[2] = now.month
            hdr[3] = now.day
        if clear_structural_index and len(hdr) > 28:
            hdr[28] &= ~FLAG_HAS_STRUCTURAL_INDEX & 0xFF

        fd, tmp_name = tempfile.mkstemp(
            dir=str(out_path.parent), prefix=f".{out_path.name}.", suffix=".tmp"
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(hdr)
                for r in records:
                    f.write(r)
                f.write(b"\x1a")  # стандартный маркер конца файла
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, out_path)  # атомарная замена в пределах ФС
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise
