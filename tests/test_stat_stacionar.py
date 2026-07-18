"""Статистика стационара: отбор случаев, три файла, десятичная запятая в CSV."""

import pytest

from omsreg.core import JobError
from omsreg.utils.stat_stacionar import run_stat


def _row(kod, kotd, kmkb, stoim, ishod):
    return (kod, "Ф", "И", kotd, kmkb, stoim, ishod)


def test_run_stat_outputs_and_split(make_dbf, registry_fields, tmp_path):
    rows = [
        _row("1", "10", "A00", "1500.50", "1"),   # дневной
        _row("2", "20", "B01", "3000.00", "7"),   # круглосуточный
        _row("3", "10", "A00", "2000.00", "9"),   # дневной
    ]
    dbf = make_dbf(tmp_path / "stat.dbf", registry_fields, rows)
    res = run_stat(str(dbf), "10", console=False)

    assert res["cases"] == 3
    for key in ("txt_path", "csv_path", "html_path", "log_path"):
        assert res[key].exists()

    csv = res["csv_path"].read_text(encoding="utf-8-sig")
    assert "1500,50" in csv          # десятичная запятая для русского Excel
    assert "1500.50" not in csv

    html = res["html_path"].read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in html
    assert "Дневной стационар" in res["text"]
    assert "Круглосуточный стационар" in res["text"]


def test_deleted_records_excluded(make_dbf, registry_fields, tmp_path):
    rows = [_row("1", "10", "A00", "100.00", "1"), _row("2", "10", "A00", "100.00", "1")]
    dbf = make_dbf(tmp_path / "s.dbf", registry_fields, rows, deleted=[False, True])
    res = run_stat(str(dbf), "10", console=False)
    assert res["cases"] == 1         # помеченная '*' запись исключена


def test_missing_field_raises_joberror(make_dbf, tmp_path):
    dbf = make_dbf(tmp_path / "bad.dbf", [("XCODE", 5)], [("1",)])
    with pytest.raises(JobError):
        run_stat(str(dbf), "10", console=False)
