"""Удаление ошибочных талонов по протоколам: разбор *.txt, план, удаление, бэкап."""

from omsreg.core.dbf import DbfTable
from omsreg.utils.remove_error_talons import parse_protocol, run_removal


def _rows(codes):
    return [(c, "Ф", "И", "10", "A00", "100.00", "1") for c in codes]


def _write_protocol(path):
    text = (
        "Обработан файл: d00902_07.dbf\n"
        "Количество ошибок - 2\n"
        "1234567890123456 нарушение код талона:111111\n"
        "1234567890123457 нарушение код\nталона:333333\n"  # перенос строки внутри записи
    )
    path.write_bytes(text.encode("cp1251"))


def test_parse_protocol_extracts_name_and_codes(tmp_path):
    p = tmp_path / "ДВ.txt"
    _write_protocol(p)
    info = parse_protocol(p)
    assert info["dbf_name"] == "d00902_07.dbf"
    assert info["declared_errors"] == 2
    assert set(info["codes"]) == {"111111", "333333"}  # код, разорванный переносом, склеен


def test_run_removal_deletes_from_named_and_common(make_dbf, registry_fields, tmp_path):
    make_dbf(tmp_path / "d00902_07.dbf", registry_fields, _rows(["111111", "222222", "333333"]))
    make_dbf(tmp_path / "6_0090207t.dbf", registry_fields, _rows(["111111", "333333", "444444"]))
    _write_protocol(tmp_path / "ДВ.txt")

    res = run_removal(str(tmp_path), console=False)
    assert res["had_error"] is False
    assert res["deleted_total"] == 4     # по 2 из именованного и общего файлов
    assert res["files_changed"] == 2

    named = DbfTable(tmp_path / "d00902_07.dbf")
    assert sorted(named.code_value(r, "KOD_TALON") for r in named.records) == [222222]
    common = DbfTable(tmp_path / "6_0090207t.dbf")
    assert sorted(common.code_value(r, "KOD_TALON") for r in common.records) == [444444]

    backup = next(tmp_path.glob("backup_*"))
    names = {p.name for p in backup.iterdir()}
    assert {"d00902_07.dbf", "6_0090207t.dbf"} <= names


def test_dry_run_leaves_files(make_dbf, registry_fields, tmp_path):
    d = make_dbf(tmp_path / "d00902_07.dbf", registry_fields, _rows(["111111", "222222"]))
    make_dbf(tmp_path / "6_0090207t.dbf", registry_fields, _rows(["111111"]))
    _write_protocol(tmp_path / "ДВ.txt")
    before = d.read_bytes()
    res = run_removal(str(tmp_path), dry_run=True, console=False)
    assert res["dry_run"] is True
    assert d.read_bytes() == before
    assert not list(tmp_path.glob("backup_*"))
