"""Чтение DBF, доступ к полям и безопасная запись (round-trip + флаг индекса)."""

import struct

import pytest

from omsreg.core.dbf import DbfTable


def test_read_fields_and_values(make_dbf, registry_fields, tmp_path):
    rows = [
        ("111111", "Иванов", "Иван", "10", "A00", "1500.50", "1"),
        ("222222", "Петров", "Пётр", "20", "B01", "3000.00", "7"),
    ]
    p = make_dbf(tmp_path / "d.dbf", registry_fields, rows)
    t = DbfTable(p)
    assert t.nrec == 2
    assert [f.name for f in t.fields][0] == "KOD_TALON"
    assert t.has_field("kod_talon")  # без учёта регистра
    assert t.value(t.records[0], "SURNAME") == "Иванов"
    assert t.value(t.records[0], t.field("NAME")) == "Иван"  # и по имени, и по дескриптору
    assert t.code_value(t.records[0], "KOD_TALON") == 111111
    assert t.int_value(t.records[1], "KOTD") == 20
    assert t.float_value(t.records[0], "STOIM") == 1500.5


def test_is_deleted_flag(make_dbf, registry_fields, tmp_path):
    rows = [("1", "A", "B", "10", "A00", "1", "1"), ("2", "C", "D", "10", "A00", "1", "1")]
    p = make_dbf(tmp_path / "d.dbf", registry_fields, rows, deleted=[False, True])
    t = DbfTable(p)
    assert not t.is_deleted(t.records[0])
    assert t.is_deleted(t.records[1])


def test_save_roundtrip_updates_count_and_trailer(make_dbf, registry_fields, tmp_path):
    rows = [("111111", "A", "B", "10", "A00", "1", "1"),
            ("222222", "C", "D", "10", "A00", "1", "1"),
            ("333333", "E", "F", "10", "A00", "1", "1")]
    p = make_dbf(tmp_path / "d.dbf", registry_fields, rows)
    t = DbfTable(p)
    kept = [r for r in t.records if t.code_value(r, "KOD_TALON") != 222222]
    t.save(kept, p)
    again = DbfTable(p)
    assert again.nrec == 2
    assert [again.code_value(r, "KOD_TALON") for r in again.records] == [111111, 333333]
    # маркер конца файла на месте
    assert p.read_bytes().endswith(b"\x1a")
    # число записей в заголовке (offset 4) обновлено
    assert struct.unpack("<I", p.read_bytes()[4:8])[0] == 2


def test_save_clears_structural_index_flag(make_dbf, registry_fields, tmp_path):
    rows = [("1", "A", "B", "10", "A00", "1", "1")]
    p = make_dbf(tmp_path / "d.dbf", registry_fields, rows)
    t = DbfTable(p)
    # взводим бит наличия .cdx в заголовке вручную и сохраняем со сбросом
    hdr = bytearray(t.header)
    hdr[28] |= 0x01
    t.header = bytes(hdr)
    t.save(t.records, p, clear_structural_index=True)
    assert DbfTable(p).header[28] & 0x01 == 0


def test_bad_header_rejected(tmp_path):
    tiny = tmp_path / "tiny.dbf"
    tiny.write_bytes(b"\x03" + b"\x00" * 10)
    with pytest.raises(ValueError):
        DbfTable(tiny)


def test_truncated_body_rejected(make_dbf, registry_fields, tmp_path):
    rows = [("1", "A", "B", "10", "A00", "1", "1")]
    p = make_dbf(tmp_path / "d.dbf", registry_fields, rows)
    data = bytearray(p.read_bytes())
    struct.pack_into("<I", data, 4, 999)  # заявим 999 записей, которых нет
    p.write_bytes(bytes(data))
    with pytest.raises(ValueError):
        DbfTable(p)
