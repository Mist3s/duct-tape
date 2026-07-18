"""Удаление по списку кодов из всех DBF папки: dry-run, реальное удаление, спутники."""

from omsreg.core.dbf import DbfTable
from omsreg.utils.remove_codes import run_codes


def _rows(codes):
    return [(c, "Ф", "И", "10", "A00", "100.00", "1") for c in codes]


def test_dry_run_changes_nothing(make_dbf, registry_fields, tmp_path):
    a = make_dbf(tmp_path / "a.dbf", registry_fields, _rows(["111111", "222222"]))
    before = a.read_bytes()
    (tmp_path / "codes.txt").write_text("111111\n", encoding="utf-8")
    res = run_codes(str(tmp_path), "codes.txt", dry_run=True, console=False)
    assert res["deleted_total"] == 1        # найдено под удаление
    assert res["dry_run"] is True
    assert a.read_bytes() == before          # но файл не тронут
    assert not list(tmp_path.glob("backup_*"))


def test_real_delete_across_files_and_companions(make_dbf, registry_fields, tmp_path):
    a = make_dbf(tmp_path / "a.dbf", registry_fields, _rows(["111111", "222222", "333333"]))
    b = make_dbf(tmp_path / "b.dbf", registry_fields, _rows(["111111", "444444"]))
    make_dbf(tmp_path / "nofield.dbf", [("XCODE", 5)], [("1",)])
    (tmp_path / "a.cdx").write_bytes(b"\x00" * 32)  # устаревший индекс
    (tmp_path / "codes.txt").write_text("111111\n333333\n", encoding="utf-8")

    res = run_codes(str(tmp_path), "codes.txt", console=False)
    assert res["had_error"] is False
    assert res["deleted_total"] == 3         # 111111 в a и b, 333333 в a
    assert res["files_changed"] == 2

    ta = DbfTable(a)
    assert sorted(ta.code_value(r, "KOD_TALON") for r in ta.records) == [222222]
    tb = DbfTable(b)
    assert sorted(tb.code_value(r, "KOD_TALON") for r in tb.records) == [444444]

    # устаревший индекс a.cdx удалён, флаг индекса в заголовке сброшен
    assert not (tmp_path / "a.cdx").exists()
    assert DbfTable(a).header[28] & 0x01 == 0
    # бэкап содержит и .dbf, и его спутник .cdx
    backup = next(tmp_path.glob("backup_*"))
    names = {p.name for p in backup.iterdir()}
    assert "a.dbf" in names and "a.cdx" in names


def test_all_files_missing_field_reports_error(make_dbf, tmp_path):
    make_dbf(tmp_path / "x.dbf", [("XCODE", 5)], [("1",)])
    (tmp_path / "codes.txt").write_text("111111\n", encoding="utf-8")
    res = run_codes(str(tmp_path), "codes.txt", console=False)
    assert res["had_error"] is True
    assert res["deleted_total"] == 0
