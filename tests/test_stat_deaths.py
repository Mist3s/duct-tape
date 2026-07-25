"""Смертность стационара: чтение HTML-выгрузки, агрегация месяц×группа, вывод .xlsx."""

import xml.etree.ElementTree as ET
import zipfile

import pytest

from omsreg.core import JobError
from omsreg.utils._shared.mkb_death_groups import load_rules
from omsreg.utils._shared.stat_deaths_report import build_html, build_report
from omsreg.utils.stat_deaths import build_matrix, read_source, run_deaths

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _tr(*cells):
    """Строка HTML-таблицы из готовых ячеек."""
    return "<tr>" + "".join(cells) + "</tr>"


def _data_row(*cells):
    """Строка данных выгрузки: № ИБ, ФИО, пол, возраст, отделение, даты, дней, диагнозы."""
    return _tr(*(f"<td>{v}</td>" for v in cells))


# выгрузка «Отчёт по умершим» — это HTML-таблица (как экспорт мед-системы)
_HEAD = (
    "<html><body>\n"
    "<p>Список пациентов (умершие за период с 01.01.2026 по 31.03.2026 )</p>\n"
    "<table>\n"
    + _tr('<td rowspan="2">№ ИБ</td>', '<td rowspan="2">Ф.И.О.</td>',
          '<td rowspan="2">Пол</td>', '<td rowspan="2">Возр.</td>',
          '<td rowspan="2">Отделение</td>', '<td rowspan="2">Дата поступл.</td>',
          '<td rowspan="2">Дата выписки</td>', '<td rowspan="2">Кол. дней</td>',
          '<td colspan="2">Диагнозы</td>')
    + "\n" + _tr("<td>Клинич.</td>", "<td>Патологоан.</td>") + "\n"
)
_TERAPIYA = "Терапевтическое отделение"
_NEVRO = "Неврологическое отделение №1"
_ROWS = "\n".join([
    _data_row(1, "А", "Мужской", 70, _TERAPIYA, "02.01.2026", "05.01.2026", 3, "I25.5"),
    _data_row(2, "Б", "Женский", 80, _TERAPIYA, "03.01.2026", "06.01.2026", 3, "I48.0"),
    _data_row(3, "В", "Мужской", 65, _NEVRO, "01.02.2026", "10.02.2026", 9, "G93.4"),
    _data_row(4, "Г", "Женский", 77, _NEVRO, "05.02.2026", "12.02.2026", 7, "I63.9"),
    _data_row(5, "Д", "Мужской", 72, "Пульмонологическое отделение",
              "02.03.2026", "08.03.2026", 6, "J18.9"),
    _data_row(6, "Е", "Женский", 60, _TERAPIYA, "03.03.2026", "09.03.2026", 6, "C34.9"),
    _data_row(7, "Ж", "Мужской", 68, _TERAPIYA, "04.03.2026", "10.03.2026", 6, "", "E11.6"),
])
_HTML = _HEAD + _ROWS + "\n</table></body></html>"


def _src(tmp_path, body=_HTML):
    p = tmp_path / "Отчет по умершим.xls"   # расширение .xls, но внутри HTML
    p.write_text(body, encoding="utf-8")
    return p


def _rules():
    rules, problems = load_rules()
    assert problems == []
    return rules


def _xlsx_rows(path):
    root = ET.fromstring(zipfile.ZipFile(path).read("xl/worksheets/sheet1.xml"))
    rows = []
    for row in root.iter(NS + "row"):
        vals = []
        for c in row.iter(NS + "c"):
            t, v, iss = c.get("t"), c.find(NS + "v"), c.find(NS + "is")
            if t == "inlineStr" and iss is not None:
                vals.append("".join(x.text or "" for x in iss.iter(NS + "t")))
            else:
                vals.append(v.text if v is not None else "")
        rows.append(vals)
    return rows


def test_read_source(tmp_path):
    src = read_source(_src(tmp_path))
    assert len(src.records) == 7
    assert src.period[0].isoformat() == "2026-01-01" and src.period[1].isoformat() == "2026-03-31"
    assert src.encoding == "utf-8-sig" and src.problems == []
    r0 = src.records[0]
    assert r0["dept"] == _TERAPIYA
    assert r0["clin"] == "I25.5" and r0["d_out"].month == 1
    # клинический пуст, есть патологоанатомический
    assert src.records[6]["clin"] == "" and src.records[6]["pat"] == "E11.6"


def test_matrix_aggregation(tmp_path):
    path = _src(tmp_path)
    src = read_source(path)
    m = build_matrix(src.records, _rules(), period=src.period, source=path)
    assert m.grand_total == 7
    gt = m.group_total
    assert gt["Болезни нервной системы"] == 2        # G93.4 + инсульт I63.9
    assert gt["Болезни кровообращения"] == 2         # I25.5 + I48.0
    assert gt["Болезни органов дыхания"] == 1        # J18.9
    assert gt["Болезни эндокринной системы"] == 1    # E11.6 из патологоанатомического
    assert m.unclassified["C34.9"] == 1 and "Прочие" in m.columns


def test_report_from_bare_matrix(tmp_path):
    # build_matrix даёт полный агрегат: отчёт строится без дописывания полей снаружи
    src = read_source(_src(tmp_path))
    data = build_matrix(src.records, _rules())
    text, table = build_report(data)
    assert "СМЕРТНОСТЬ" in text and "по датам выбытия" in text
    assert table[0][:2] == ["Месяц", "Всего"] and table[-1][0] == "Итого"
    assert "Смертность" in build_html(data)


def test_run_deaths_outputs_xlsx(tmp_path):
    res = run_deaths(str(_src(tmp_path)), console=False)
    assert res["deaths"] == 7 and res["unclassified"] == 1
    for key in ("txt_path", "xlsx_path", "html_path"):
        assert res[key].exists()
    assert "СМЕРТНОСТЬ 2026" in res["text"]

    rows = _xlsx_rows(res["xlsx_path"])
    assert rows[0][:2] == ["Месяц", "Всего"]
    circ = rows[0].index("Болезни кровообращения")
    jan = next(r for r in rows if r[0] == "Январь")
    assert "ИБС 1" in jan[circ] and "фибрилляция 1" in jan[circ]     # приписка «в т.ч.» в месяце
    itog = rows[-1]
    assert itog[0] == "Итого" and itog[1] == "7"                     # в «Итого» только «Всего»…
    assert itog[circ] == ""                                          # …столбцы групп пустые (как в образце)

    html = res["html_path"].read_text(encoding="utf-8")
    assert "Смертность 2026" in html and "Прочие" in html


def test_patho_priority(tmp_path):
    # с приоритетом патологоанатомического клинический I25.5 у 1-й записи игнорируется,
    # но у неё патолог. пуст -> берётся клинический; проверяем, что режим не падает
    res = run_deaths(str(_src(tmp_path)), patho_priority=True, console=False)
    assert res["deaths"] == 7


def test_bad_source_raises(tmp_path):
    p = tmp_path / "x.xls"
    p.write_text("совсем не таблица", encoding="utf-8")
    with pytest.raises(JobError):
        run_deaths(str(p), console=False)


# ----------------------------- диагностика пропущенного -----------------------------

_BROKEN = _HEAD + "\n".join([
    _data_row(1, "А", "Мужской", 70, _TERAPIYA, "02.01.2026", "05.01.2026", 3, "I25.5"),
    # № ИБ буквенно-цифровой — запись отбрасывается, но не молча
    _data_row("А-8", "З", "Женский", 90, _TERAPIYA, "04.01.2026", "07.01.2026", 3, "I21.0"),
    # возраст не в годах
    _data_row(2, "И", "Мужской", "7 мес.", _TERAPIYA, "05.01.2026", "09.01.2026", 4, "J18.9"),
    # даты в чужом формате -> запись без даты выбытия
    _data_row(3, "К", "Женский", 90, _TERAPIYA, "2026-01-08", "2026-01-09", 1, "I21.0"),
]) + "\n</table></body></html>"


def test_source_problems_are_collected(tmp_path):
    src = read_source(_src(tmp_path, _BROKEN))
    assert len(src.records) == 3                      # строка с № ИБ «А-8» отброшена
    joined = " | ".join(src.problems)
    assert "А-8" in joined and "нечисловым № ИБ" in joined
    assert "2026-01-08" in joined and "ДД.ММ.ГГГГ" in joined


def test_unparsed_age_is_counted(tmp_path):
    src = read_source(_src(tmp_path, _BROKEN))
    m = build_matrix(src.records, _rules(), period=src.period)
    assert m.ages == [70]                             # «7 мес.» в годы не превращаем
    assert m.age_unparsed["7 мес."] == 1
    assert m.no_date == 1                             # запись с датами «2026-01-08» не учтена
    assert m.grand_total == 2


def test_run_deaths_logs_problems(tmp_path):
    groups = tmp_path / "g.csv"
    groups.write_text("код_от;код_до;группа;подгруппа\n"
                      "I2O;I25;Болезни кровообращения;ИБС\n"
                      "I00;I99;Болезни кровообращения;\n", encoding="utf-8")
    res = run_deaths(str(_src(tmp_path, _BROKEN)), groups=str(groups), console=False)
    journal = res["log_path"].read_text(encoding="utf-8")
    assert "справочник групп" in journal and "I2O" in journal      # битое правило справочника
    assert "нечисловым № ИБ" in journal                            # потерянная строка данных
    assert "не разобрано дат" in journal                           # дата в чужом формате
    assert "возраст не разобран" in journal                        # «7 мес.»
    assert "коды МКБ вне справочника" in journal                   # J18.9 вне своего справочника
