"""Текстовый, табличный (.xlsx) и HTML-отчёт «Смертность стационара».

Все три формы строятся из одного агрегата (stat_deaths.build_matrix -> DeathsMatrix)
и показывают одни и те же числа: распределение умерших по месяцам и группам причин
смерти (класс МКБ), с подгруппами (ИБС, фибрилляция и т.п.) в приписке «(в т.ч. …)».

Таблица для .xlsx и HTML повторяют разметку ручного отчёта: строки — месяцы,
столбцы — группы, столбец «Всего», строка «Итого». Текстовый отчёт для читаемости
в консоли развёрнут наоборот (строки — группы, столбцы — месяцы). HTML
самодостаточен (тема из core.report_html + правила ниже).
"""

from __future__ import annotations

import html

from omsreg.core import pct
from omsreg.core.format import report_stamp
from omsreg.core.report_html import page, tile

MONTHS_RU = ["", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
             "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]
MONTHS_SHORT = ["", "Янв", "Фев", "Мар", "Апр", "Май", "Июн",
                "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек"]

# ширины колонок текстовой матрицы: название группы, «Всего», месяц
NAME_WIDTH = 40
TOTAL_WIDTH = 7
MONTH_WIDTH = 6
# приписка «в т.ч. …» сдвинута внутри колонки названия — и потому уже
SUB_INDENT = 2
SUB_NAME_WIDTH = NAME_WIDTH - SUB_INDENT
# ширины колонок текстовой таблицы «по отделениям»: умерших, доля
DEPT_COUNT_WIDTH = 6
DEPT_SHARE_WIDTH = 8

_CSS = (
    "@media (prefers-color-scheme: dark) { :root { --ink:#fff; } }\n"
    "body { font:15px/1.5 system-ui,\"Segoe UI\",Roboto,sans-serif; }\n"
    ".wrap { max-width:1100px; }\n"
    "h2 { font-size:18px; margin:28px 0 8px; }\n"
    ".meta { margin-bottom:16px; }\n"
    ".note { color:var(--ink2); font-size:13px; margin:6px 0; }\n"
    ".warn { color:#b26a00; }\n"
    "@media (prefers-color-scheme: dark) { .warn { color:#e0a24a; } }\n"
    ".tiles { margin:14px 0; }\n"
    ".sub { color:var(--ink2); font-size:12px; white-space:nowrap; }\n"
    "tbody tr:hover td { background:var(--hover); }\n"
    "td.g { text-align:right; white-space:nowrap; }\n"
    # матрица месяц×группа: не растягиваем на всю ширину, длинные заголовки групп переносим
    ".matrix { width:auto; font-size:13px; }\n"
    ".matrix th, .matrix td { padding:5px 10px; }\n"
    ".matrix th { white-space:normal; vertical-align:bottom; max-width:104px; }\n"
    # числовые колонки (Всего и группы) выравниваем по центру — заголовок и число под ним совпадают
    ".matrix th.num, .matrix td.num, .matrix td.g { text-align:center; }\n"
    ".matrix td.g { white-space:normal; }\n"
    ".matrix .sub { text-align:center; }\n"
    ".matrix th:first-child, .matrix th.num:nth-child(2) { white-space:nowrap; }\n"
    ".matrix td:first-child { white-space:nowrap; text-align:left; }\n"
)


def period_str(period) -> str:
    """Период отчёта для шапки: «01.01.2026–31.03.2026» или «по датам выбытия».

    Единственное место, где период превращается в текст: и журнал задачи, и обе
    формы отчёта пишут его одинаково.
    """
    if period and period[0] and period[1]:
        return f"{period[0]:%d.%m.%Y}–{period[1]:%d.%m.%Y}"
    return "по датам выбытия"


def _multi_year(months) -> bool:
    return len({y for y, _ in months}) > 1


def _mlabel(ym, short=False, multiyear=False) -> str:
    y, m = ym
    name = (MONTHS_SHORT if short else MONTHS_RU)[m]
    return f"{name}.{y % 100:02d}" if multiyear else name


def _cell(n: int, detail: list[tuple[str, int]]):
    """Значение ячейки группы для таблицы .xlsx.

    Число n либо «7 (в т.ч. ИБС 6, фибрилляция 1)», если у группы есть непустые
    подгруппы. Возвращает int (чтобы Excel считал это числом) либо строку.
    """
    parts = [f"{s} {v}" for s, v in detail if v]
    return f"{n} (в т.ч. {', '.join(parts)})" if parts else n


def _sub_totals(data):
    """{(group, sub): всего за все месяцы} — для строки/столбца «Итого»."""
    out = {}
    for (_ym, g, s), v in data.subcount.items():
        out[(g, s)] = out.get((g, s), 0) + v
    return out


# ----------------------------- текст + таблица для .xlsx -----------------------------

def xlsx_columns(data) -> dict:
    """Ширины столбцов для .xlsx, как в образце.

    Месяц и «Всего» узкие, группы шире, а группа с подгруппами (в её ячейках
    появляется приписка «в т.ч. …») — самая широкая.
    """
    subtot = _sub_totals(data)
    widths = {0: 12, 1: 9}   # Месяц, Всего
    for i, g in enumerate(data.columns, start=2):
        has_sub = any(subtot.get((g, s)) for s in data.subs.get(g, []))
        widths[i] = 36 if has_sub else 19
    return widths


def build_report(data):
    """-> (текст отчёта строкой, таблица месяц×группа для .xlsx (список списков))."""
    out = []
    w = out.append
    months = data.months
    cols = data.columns
    subs = data.subs
    count = data.count
    subcount = data.subcount
    tmonth = data.total_month
    gtotal = data.group_total
    grand = data.grand_total
    subtot = _sub_totals(data)
    multiyear = _multi_year(months)

    w("=" * 100)
    w(f"СМЕРТНОСТЬ {data.year or ''} — СТАЦИОНАР".strip())
    w(f"Сформировано: {report_stamp()}")
    w(f"Источник: {data.source_name}; период: {period_str(data.period)}; умерших: {grand}"
      + (f" (без даты выбытия, не учтено: {data.no_date})" if data.no_date else ""))
    w("=" * 100)

    # ---------- матрица: строки — группы, столбцы — месяцы (читаемо в консоли) ----------
    w("")
    w("РАСПРЕДЕЛЕНИЕ ПО ПРИЧИНАМ И МЕСЯЦАМ (строки — группы, столбцы — месяцы)")
    head = f"  {'группа причин':<{NAME_WIDTH}}{'Всего':>{TOTAL_WIDTH}}" + "".join(
        f"{_mlabel(ym, short=True, multiyear=multiyear):>{MONTH_WIDTH}}" for ym in months)
    w(head)
    w("  " + "-" * (len(head) - 2))
    for g in cols:
        cells = "".join(f"{count.get((ym, g), 0):>{MONTH_WIDTH}}" for ym in months)
        w(f"  {g[:NAME_WIDTH]:<{NAME_WIDTH}}{gtotal.get(g, 0):>{TOTAL_WIDTH}}{cells}")
        for s in subs.get(g, []):
            if not subtot.get((g, s)):
                continue
            label = ("в т.ч. " + s)[:SUB_NAME_WIDTH]
            cells = "".join(f"{subcount.get((ym, g, s), 0):>{MONTH_WIDTH}}" for ym in months)
            w(f"    {label:<{SUB_NAME_WIDTH}}{subtot[(g, s)]:>{TOTAL_WIDTH}}{cells}")
    w("  " + "-" * (len(head) - 2))
    w(f"  {'ВСЕГО':<{NAME_WIDTH}}{grand:>{TOTAL_WIDTH}}"
      + "".join(f"{tmonth.get(ym, 0):>{MONTH_WIDTH}}" for ym in months))

    # ---------- пояснения / проблемные коды ----------
    if data.unclassified:
        w("")
        w("КОДЫ МКБ ВНЕ СПРАВОЧНИКА (учтены в столбце «Прочие»):")
        w("  " + ", ".join(f"{c}×{n}" for c, n in data.unclassified.most_common()))
        w("  При необходимости дополните справочник групп (mkb_death_groups.csv или --groups).")

    # ---------- по отделениям ----------
    if data.by_dept:
        w("")
        w("ПО ОТДЕЛЕНИЯМ")
        for dept, n in data.by_dept.most_common():
            w(f"  {dept:<{NAME_WIDTH}}{n:>{DEPT_COUNT_WIDTH}}"
              f"{pct(n, grand):>{DEPT_SHARE_WIDTH}}")

    # ---------- средний возраст ----------
    if data.ages:
        a = data.ages
        w("")
        w(f"ВОЗРАСТ УМЕРШИХ: средний {sum(a) / len(a):.1f}, от {min(a)} до {max(a)} лет "
          f"(указан у {len(a)} из {grand})")

    # ---------- методика ----------
    w("")
    w("=" * 100)
    w("МЕТОДИКА")
    w("  • Месяц определяется по дате выбытия (смерти); группа — по коду МКБ клинического")
    w("    диагноза (патологоанатомический берётся, если клинический пуст).")
    w("  • Распределение по группам — из справочника МКБ→группа; коды вне него идут в «Прочие».")
    w("  • «(в т.ч. …)» — подгруппы внутри группы (например ИБС, фибрилляция в болезнях")
    w("    кровообращения), заданные в справочнике.")
    w(f"  Источник данных: {data.source or data.source_name}.")
    w("=" * 100)

    # ---------- таблица для .xlsx: как ручной отчёт (строки — месяцы, столбцы — группы) ----------
    table = [["Месяц", "Всего"] + cols]
    for ym in months:
        row = [_mlabel(ym, multiyear=multiyear), tmonth.get(ym, 0)]
        for g in cols:
            detail = [(s, subcount.get((ym, g, s), 0)) for s in subs.get(g, [])]
            row.append(_cell(count.get((ym, g), 0), detail))
        table.append(row)
    # строка «Итого»: как в образце — заполнены только «Месяц» и «Всего», столбцы групп пустые
    table.append(["Итого", grand] + ["" for _ in cols])

    return "\n".join(out), table


# ----------------------------- HTML -----------------------------

def build_html(data) -> str:
    """-> HTML-страница отчёта (самодостаточная) из того же агрегата DeathsMatrix.

    Отличие от таблицы для .xlsx намеренное: в HTML строка «Итого» заполнена по
    группам (в браузере это удобно), а в .xlsx столбцы групп в «Итого» пустые —
    как в ручном отчёте-образце.
    """
    e = html.escape
    months = data.months
    cols = data.columns
    subs = data.subs
    count = data.count
    subcount = data.subcount
    tmonth = data.total_month
    gtotal = data.group_total
    grand = data.grand_total
    subtot = _sub_totals(data)
    multiyear = _multi_year(months)
    parts = []
    p = parts.append

    p(f"<h1>Смертность {data.year or ''} — стационар</h1>")
    p(f'<div class="meta">Сформировано {report_stamp(seconds=False)} · источник: '
      f'{e(data.source_name)} · период: {e(period_str(data.period))}</div>')

    p('<div class="tiles">')
    p(tile(grand, "умерших всего"))
    p(tile(len(months), "месяцев в отчёте"))
    if data.ages:
        a = data.ages
        p(tile(f"{sum(a) / len(a):.0f}", f"средний возраст (от {min(a)} до {max(a)})"))
    if data.no_date:
        p(tile(data.no_date, "без даты выбытия (не учтены)", "bad"))
    p('</div>')

    # ---------- матрица месяц × группа (как ручной отчёт) ----------
    p("<h2>По месяцам и группам причин</h2>")
    p('<table class="matrix"><thead><tr><th>Месяц</th><th class="num">Всего</th>'
      + "".join(f'<th class="num">{e(g)}</th>' for g in cols)
      + "</tr></thead><tbody>")

    def gcell(cnt, detail):
        parts_ = [f"{e(s)} {v}" for s, v in detail if v]
        sub = f'<div class="sub">в т.ч. {", ".join(parts_)}</div>' if parts_ else ""
        return f'<td class="g">{cnt}{sub}</td>'

    for ym in months:
        cells = ""
        for g in cols:
            detail = [(s, subcount.get((ym, g, s), 0)) for s in subs.get(g, [])]
            cells += gcell(count.get((ym, g), 0), detail)
        p(f'<tr><td>{e(_mlabel(ym, multiyear=multiyear))}</td>'
          f'<td class="num">{tmonth.get(ym, 0)}</td>{cells}</tr>')
    itog = ""
    for g in cols:
        detail = [(s, subtot.get((g, s), 0)) for s in subs.get(g, [])]
        itog += gcell(gtotal.get(g, 0), detail)
    p(f'<tr class="total"><td>Итого</td><td class="num">{grand}</td>{itog}</tr>')
    p("</tbody></table>")

    # ---------- проблемные коды ----------
    if data.unclassified:
        codes = ", ".join(f"{e(c)}×{n}" for c, n in data.unclassified.most_common())
        p(f'<p class="note warn">Коды МКБ вне справочника (учтены в «Прочие»): {codes}. '
          "При необходимости дополните справочник групп.</p>")

    # ---------- по отделениям ----------
    if data.by_dept:
        p("<h2>По отделениям</h2>")
        p('<table><thead><tr><th>Отделение</th><th class="num">умерших</th>'
          '<th class="num">доля</th></tr></thead><tbody>')
        for dept, n in data.by_dept.most_common():
            p(f'<tr><td>{e(dept)}</td><td class="num">{n}</td>'
              f'<td class="num">{pct(n, grand)}</td></tr>')
        p("</tbody></table>")

    p('<p class="note">Месяц — по дате выбытия; группа — по коду МКБ клинического диагноза '
      "(патологоанатомический — если клинический пуст). Распределение по группам из справочника "
      "МКБ→группа; «в т.ч.» — подгруппы (ИБС, фибрилляция и т.п.).</p>")

    return page(f"Смертность {data.year or ''} — {e(data.source_name)}",
                "\n".join(parts), extra_css=_CSS)
