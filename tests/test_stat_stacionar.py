"""Статистика стационара: отбор случаев, три файла, десятичная запятая в CSV."""

import pytest

from omsreg.core import DbfTable, JobError, money
from omsreg.utils._shared.stat_common import DAY_TYPE, ROUND_TYPE, Case
from omsreg.utils._shared.stat_stacionar_report import build_html, build_report, prepare
from omsreg.utils.stat_stacionar import collect, run_stat


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


def test_outcomes_by_stationar_and_department(make_dbf, registry_fields, tmp_path):
    rows = [
        _row("1", "10", "A00", "100.00", "1"),    # дневной
        _row("2", "27", "I11.9", "200.00", "1"),  # круглосуточный, терапия
        _row("3", "27", "I25.5", "300.00", "5"),
    ]
    dbf = make_dbf(tmp_path / "s.dbf", registry_fields, rows)
    res = run_stat(str(dbf), "10", console=False)
    txt = res["text"]
    assert "ИСХОДЫ ПО СТАЦИОНАРАМ И ОТДЕЛЕНИЯМ" in txt
    assert "27 — Терапевтическое" in txt          # название отделения из KOTD_NAMES
    html = res["html_path"].read_text(encoding="utf-8")
    assert "Исходы по отделениям" in html
    assert "Терапевтическое" in html


def test_deleted_records_excluded(make_dbf, registry_fields, tmp_path):
    rows = [_row("1", "10", "A00", "100.00", "1"), _row("2", "10", "A00", "100.00", "1")]
    dbf = make_dbf(tmp_path / "s.dbf", registry_fields, rows, deleted=[False, True])
    res = run_stat(str(dbf), "10", console=False)
    assert res["cases"] == 1         # помеченная '*' запись исключена


def test_missing_field_raises_joberror(make_dbf, tmp_path):
    dbf = make_dbf(tmp_path / "bad.dbf", [("XCODE", 5)], [("1",)])
    with pytest.raises(JobError):
        run_stat(str(dbf), "10", console=False)


def test_collect_returns_named_cases(make_dbf, registry_fields, tmp_path):
    """Случай из collect — это Case: поля доступны по имени, распаковка совместима."""
    rows = [_row("1", "10", "A00", "1500.50", "1"), _row("2", "20", "B01", "3000.00", "7")]
    dbf = make_dbf(tmp_path / "c.dbf", registry_fields, rows)
    cases, deleted, has_fact = collect(DbfTable(dbf), {10})

    day, round_ = cases
    assert isinstance(day, Case)
    assert tuple(day) == (day.st_type, day.kotd, day.kmkb, day.ishod, day.stoim, day.fact)
    assert (day.st_type, day.kotd, day.kmkb, day.ishod, day.stoim) == (DAY_TYPE, 10, "A00", 1, 1500.5)
    assert round_.st_type == ROUND_TYPE
    assert day.fact is None and has_fact is False   # поля FACT в файле нет
    assert deleted == 0


def test_prepare_aggregates_once_for_both_forms(make_dbf, registry_fields, tmp_path):
    """Слой агрегации: группировки считаются один раз, .txt и .html берут их же цифры."""
    rows = [
        _row("1", "10", "A00", "100.00", "1"),    # дневной
        _row("2", "27", "I11.9", "200.00", "1"),  # круглосуточный
        _row("3", "27", "I11.9", "300.00", "9"),
    ]
    dbf = make_dbf(tmp_path / "p.dbf", registry_fields, rows)
    cases, _deleted, has_fact = collect(DbfTable(dbf), {10})
    view = prepare(cases, has_fact)

    assert view.n == 3
    assert view.total_sum == pytest.approx(600.0)
    assert view.total_fact == 0                                  # поля FACT нет
    assert sorted(view.by_type) == sorted((DAY_TYPE, ROUND_TYPE))
    assert view.by_type[ROUND_TYPE].total == pytest.approx(500.0)
    assert view.by_type[ROUND_TYPE].kotds == [27]
    assert sorted(view.by_ishod) == [1, 9]
    assert len(view.by_kotd) == 2                                # (тип, отделение)

    text, csv_rows = build_report(dbf, cases, 0, has_fact, {10}, len(rows))
    html = build_html(dbf, cases, 0, has_fact, {10}, len(rows))
    assert f"ВСЕГО: случаев 3, сумма {money(600.0)}" in text[-1]
    assert f"Всего: случаев 3, сумма {money(600.0)}" in html     # те же цифры в HTML
    assert csv_rows[-1][:5] == ["ВСЕГО", "", "", "", 3]


def test_empty_type_reported_in_both_forms(make_dbf, registry_fields, tmp_path):
    """Пустой тип стационара: и в тексте, и в HTML честное «случаев нет»."""
    rows = [_row("1", "27", "I11.9", "100.00", "1")]
    dbf = make_dbf(tmp_path / "e.dbf", registry_fields, rows)
    res = run_stat(str(dbf), "99", console=False)               # дневных отделений нет

    assert f"{DAY_TYPE.upper()}: случаев нет" in res["text"]
    assert "случаев нет" in res["html_path"].read_text(encoding="utf-8")
