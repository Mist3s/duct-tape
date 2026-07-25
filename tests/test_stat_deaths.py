"""Смертность стационара: чтение HTML-выгрузки, агрегация месяц×группа, вывод .xlsx."""

import xml.etree.ElementTree as ET
import zipfile

import pytest

from omsreg.core import JobError
from omsreg.utils._shared.mkb_death_groups import load_rules
from omsreg.utils.stat_deaths import build_matrix, read_source, run_deaths

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

# выгрузка «Отчёт по умершим» — это HTML-таблица (как экспорт мед-системы)
_HTML = """<html><body>
<p>Список пациентов (умершие за период с 01.01.2026 по 31.03.2026 )</p>
<table>
<tr><td rowspan="2">№ ИБ</td><td rowspan="2">Ф.И.О.</td><td rowspan="2">Пол</td>
<td rowspan="2">Возр.</td><td rowspan="2">Отделение</td><td rowspan="2">Дата поступл.</td>
<td rowspan="2">Дата выписки</td><td rowspan="2">Кол. дней</td><td colspan="2">Диагнозы</td></tr>
<tr><td>Клинич.</td><td>Патологоан.</td></tr>
<tr><td>1</td><td>А</td><td>Мужской</td><td>70</td><td>Терапевтическое отделение</td><td>02.01.2026</td><td>05.01.2026</td><td>3</td><td>I25.5</td><td></td></tr>
<tr><td>2</td><td>Б</td><td>Женский</td><td>80</td><td>Терапевтическое отделение</td><td>03.01.2026</td><td>06.01.2026</td><td>3</td><td>I48.0</td><td></td></tr>
<tr><td>3</td><td>В</td><td>Мужской</td><td>65</td><td>Неврологическое отделение №1</td><td>01.02.2026</td><td>10.02.2026</td><td>9</td><td>G93.4</td><td></td></tr>
<tr><td>4</td><td>Г</td><td>Женский</td><td>77</td><td>Неврологическое отделение №1</td><td>05.02.2026</td><td>12.02.2026</td><td>7</td><td>I63.9</td><td></td></tr>
<tr><td>5</td><td>Д</td><td>Мужской</td><td>72</td><td>Пульмонологическое отделение</td><td>02.03.2026</td><td>08.03.2026</td><td>6</td><td>J18.9</td><td></td></tr>
<tr><td>6</td><td>Е</td><td>Женский</td><td>60</td><td>Терапевтическое отделение</td><td>03.03.2026</td><td>09.03.2026</td><td>6</td><td>C34.9</td><td></td></tr>
<tr><td>7</td><td>Ж</td><td>Мужской</td><td>68</td><td>Терапевтическое отделение</td><td>04.03.2026</td><td>10.03.2026</td><td>6</td><td></td><td>E11.6</td></tr>
</table></body></html>"""


def _src(tmp_path):
    p = tmp_path / "Отчет по умершим.xls"   # расширение .xls, но внутри HTML
    p.write_text(_HTML, encoding="utf-8")
    return p


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
    recs, period = read_source(_src(tmp_path))
    assert len(recs) == 7
    assert period[0].isoformat() == "2026-01-01" and period[1].isoformat() == "2026-03-31"
    r0 = recs[0]
    assert r0["dept"] == "Терапевтическое отделение"
    assert r0["clin"] == "I25.5" and r0["d_out"].month == 1
    assert recs[6]["clin"] == "" and recs[6]["pat"] == "E11.6"   # клинический пуст, есть патолог.


def test_matrix_aggregation(tmp_path):
    recs, _ = read_source(_src(tmp_path))
    m = build_matrix(recs, load_rules())
    assert m["grand_total"] == 7
    gt = m["group_total"]
    assert gt["Болезни нервной системы"] == 2        # G93.4 + инсульт I63.9
    assert gt["Болезни кровообращения"] == 2         # I25.5 + I48.0
    assert gt["Болезни органов дыхания"] == 1        # J18.9
    assert gt["Болезни эндокринной системы"] == 1    # E11.6 из патологоанатомического
    assert m["unclassified"]["C34.9"] == 1 and "Прочие" in m["columns"]


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
