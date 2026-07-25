"""Общие примитивы отчётов: метка времени, полоса величины, группировка случаев."""

import re

from omsreg.core.format import report_stamp
from omsreg.core.report_html import bar
from omsreg.utils._shared.stat_common import Case, group_by


def test_report_stamp_formats():
    # текстовые формы отчётов пишут время с секундами, HTML — без
    assert re.fullmatch(r"\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}:\d{2}", report_stamp())
    assert re.fullmatch(r"\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}", report_stamp(seconds=False))


def test_bar_markup_and_clamp():
    # статистика стационара: один знак после запятой в ширине полосы
    assert bar(0.5, "100,00") == ('<div class="barwrap"><div class="bar" style="width:50.0%"></div>'
                                  '<span>100,00</span></div>')
    # экономика: ширина без дробной части
    assert 'style="width:33%"' in bar(1 / 3, "x", digits=0)
    # доля вне 0..1 не рвёт разметку
    assert 'style="width:100.0%"' in bar(1.5, "x")
    assert 'style="width:0.0%"' in bar(-1, "x")


def test_group_by_keeps_first_appearance_order():
    cases = [
        Case("Круглосуточный стационар", 27, "I11.9", 1, 100.0, 3),
        Case("Дневной стационар", 10, "A00", 1, 200.0, 1),
        Case("Круглосуточный стационар", 61, "G93.4", 9, 300.0, 5),
    ]
    by_type = group_by(cases, lambda c: c.st_type)
    # порядок групп — по первому появлению, внутри группы порядок чтения DBF
    assert list(by_type) == ["Круглосуточный стационар", "Дневной стационар"]
    assert [c.kotd for c in by_type["Круглосуточный стационар"]] == [27, 61]
    # случай-словарь экономики группируется тем же помощником
    dicts = [{"kotd": 10}, {"kotd": None}, {"kotd": 10}]
    assert [len(v) for v in group_by(dicts, lambda c: c["kotd"]).values()] == [2, 1]
