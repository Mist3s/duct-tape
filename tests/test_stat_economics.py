"""Экономика стационара: разложение стоимости КСГ, прерванность, доходность койки."""

import pytest

from omsreg.core import JobError
from omsreg.utils.stat_economics import base_rate, full_payment, run_economics, underpaid

ECON_FIELDS = [("KOTD", 2), ("KMKB", 6), ("STOIM", 10), ("FACT", 3), ("ISHOD", 2),
               ("GRUPPA", 10), ("KOEF_Z", 8), ("KOEF_UP", 8), ("KOEF_PR", 6)]


def test_cost_model_helpers():
    assert base_rate(30000, 0.9, 1.0, 1.0) == pytest.approx(33333.33, abs=0.01)
    assert base_rate(0, 0.9, 1.0, 1.0) is None       # нет данных
    assert full_payment(9000, 0.3) == pytest.approx(30000)
    assert underpaid(9000, 0.3) == pytest.approx(21000)   # недополучено
    assert underpaid(30000, 1.0) == 0                # полный случай — потерь нет


def test_economics_report(make_dbf, tmp_path):
    rows = [
        ("27", "I11.9", "30000.00", "10", "1", "st27.005", "0.90000", "1.00000", "1.0"),   # полный
        ("61", "G93.4", "9000.00", "2", "1", "st61.001", "0.90000", "1.00000", "0.3"),      # прерван
        ("10", "A00", "15000.00", "8", "1", "ds15.001", "0.70000", "0.90000", "1.0"),       # дневной
    ]
    dbf = make_dbf(tmp_path / "e.dbf", ECON_FIELDS, rows)
    res = run_economics(str(dbf), "10", console=False)

    assert res["cases"] == 3
    assert res["total"] == pytest.approx(54000)
    assert res["underpaid"] == pytest.approx(21000)   # 30000 (полная) − 9000 (факт)

    txt = res["text"]
    assert "ГЛАВНОЕ" in txt                            # ключевые выводы наверху
    assert "КАК ОТРАБОТАЛИ ОТДЕЛЕНИЯ" in txt
    assert "ГДЕ НЕДОПОЛУЧЕНО" in txt                   # разбор недооплаты по причинам
    assert "МЕТОДИКА" in txt                           # пояснения вынесены в конец
    assert "КСГ:" in txt                               # расшифровка КСГ примерами диагнозов
    assert "21 000" in txt                             # упущенная выручка
    assert "27 — Терапевтическое" in txt               # название отделения из KOTD_NAMES
    assert res["html_path"].exists()
    assert "Экономика" in res["html_path"].read_text(encoding="utf-8")


def test_economics_without_coefficients(make_dbf, tmp_path):
    # только базовые поля — отчёт всё равно строится (разделы по коэффициентам сокращаются)
    rows = [("27", "30000.00", "10"), ("27", "20000.00", "8")]
    dbf = make_dbf(tmp_path / "e.dbf", [("KOTD", 2), ("STOIM", 10), ("FACT", 3)], rows)
    res = run_economics(str(dbf), "10", console=False)
    assert res["cases"] == 2
    assert res["underpaid"] == 0                      # нет KOEF_PR — прерванность не считается
    assert "КАК ОТРАБОТАЛИ ОТДЕЛЕНИЯ" in res["text"]


def test_economics_missing_stoim_raises(make_dbf, tmp_path):
    dbf = make_dbf(tmp_path / "bad.dbf", [("KOTD", 2)], [("27",)])
    with pytest.raises(JobError):
        run_economics(str(dbf), "10", console=False)
