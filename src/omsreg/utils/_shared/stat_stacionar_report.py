"""Текстовый (+CSV) и HTML-отчёт статистики стационара.

Обе формы строятся из одного списка случаев и показывают одни и те же цифры:
разбивка по типам стационара, отделениям, исходам и таблицы стационар -> МКБ ->
исход. HTML самодостаточен (тема из core.report_html + свои правила ниже).

Порядок работы в модуле: prepare() считает все группировки (StatView) — по одному разу
на форму отчёта, — дальше функции-разделы только форматируют готовые агрегаты, ничего
не пересчитывая. По построению .txt, .csv и .html не могут разойтись в цифрах или в
порядке строк.
"""

from __future__ import annotations

import html
from typing import NamedTuple

from omsreg.core import csv_num, money, pct
from omsreg.core.format import report_stamp
from omsreg.core.report_html import bar, page, tile
from omsreg.utils._shared.stat_common import (
    DAY_TYPE,
    ROUND_TYPE,
    group_by,
    ishod_name,
    kotd_name,
)

# разделители текстовой формы: рамка разделов и линейки главных таблиц
_RULE = "=" * 100
_TABLE_RULE = "-" * 64

# шапка таблицы для CSV (столбцы дальше заполняются в том же порядке)
_CSV_HEADER = ["тип стационара", "KMKB", "код исхода", "исход",
               "случаев", "сумма STOIM", "средняя стоимость",
               "мин. стоимость случая", "макс. стоимость случая"]

# Специфичные для статистики правила поверх общей темы (core.report_html.BASE_CSS):
# ширина колонки, свои отступы, интерактивные строки (diag/ishod), панель фильтра.
_STAT_CSS = (
    "@media (prefers-color-scheme: dark) { :root { --ink:#ffffff; } }\n"
    "body { font:15px/1.5 system-ui, \"Segoe UI\", Roboto, sans-serif; }\n"
    ".wrap { max-width:1080px; }\n"
    "h2 { font-size:18px; margin:34px 0 10px; }\n"
    ".meta { margin-bottom:18px; }\n"
    ".tiles { margin:18px 0; }\n"
    "table { margin:8px 0 4px; }\n"
    "td { vertical-align:middle; }\n"
    "tr.diag:hover td, table.plain tbody tr:hover td { background:var(--hover); }\n"
    "tr.ishod td { color:var(--ink2); font-size:13px; border-bottom:1px dashed var(--border); }\n"
    "tr.ishod td:first-child { padding-left:34px; }\n"
    "tr.total td { border-bottom:none; }\n"
    ".barwrap { min-width:130px; }\n"
    ".controls { display:flex; gap:12px; align-items:center; margin:14px 0 4px; flex-wrap:wrap; }\n"
    ".controls input { padding:6px 10px; border:1px solid var(--border); border-radius:8px;\n"
    "  background:var(--card); color:var(--ink); font-size:14px; width:240px; }\n"
    ".controls button { padding:6px 12px; border:1px solid var(--border); border-radius:8px;\n"
    "  background:var(--card); color:var(--ink); font-size:14px; cursor:pointer; }\n"
    ".controls button:hover, .controls input:focus { border-color:var(--accent); outline:none; }\n"
    "body.no-ishod tr.ishod { display:none; }\n"
    "@media print { .controls { display:none; } }\n"
)

# Клиентский JS отчёта: фильтр по коду МКБ и переключатель показа строк-исходов.
_STAT_SCRIPT = """<script>
document.getElementById('tgl').onclick = function () {
  document.body.classList.toggle('no-ishod');
};
document.getElementById('flt').oninput = function () {
  var q = this.value.trim().toUpperCase();
  document.querySelectorAll('tr[data-kmkb]').forEach(function (tr) {
    tr.style.display = (!q || tr.getAttribute('data-kmkb').indexOf(q) !== -1) ? '' : 'none';
  });
};
</script>"""


# --- слой агрегации: считается в prepare(), обе формы отчёта только форматируют ---

class OutcomeStat(NamedTuple):
    """Агрегат по одному исходу внутри выборки случаев."""

    ishod: int | None     # код исхода (ISHOD); None, если не указан
    count: int            # случаев с этим исходом
    total: float          # сумма STOIM
    low: float            # минимальная стоимость случая
    high: float           # максимальная стоимость случая


class TypeStat(NamedTuple):
    """Агрегаты по одному типу стационара (дневной или круглосуточный)."""

    cases: list           # случаи этого типа в порядке чтения DBF
    total: float          # сумма STOIM по типу
    kotds: list           # коды отделений по возрастанию, без None
    by_kotd: dict         # код отделения -> случаи
    by_kmkb: dict         # код МКБ -> случаи


class StatView(NamedTuple):
    """Все группировки отчёта: и текстовая форма, и HTML читают только их."""

    cases: list           # все отобранные случаи
    n: int                # случаев всего
    total_sum: float      # сумма STOIM по всем случаям
    total_fact: float     # койко-дней (FACT); 0, если поля нет в файле
    by_type: dict         # тип стационара -> TypeStat, только непустые типы
    by_kotd: dict         # (тип стационара, код отделения) -> случаи
    by_ishod: dict        # код исхода -> случаи (весь файл)


def _total(cases) -> float:
    """Сумма STOIM по выборке случаев."""
    return sum(c.stoim for c in cases)


def _costs(cases) -> list:
    """Стоимости случаев выборки — для мин/макс и CSV."""
    return [c.stoim for c in cases]


def _by_ishod(cases) -> dict:
    """Группировка выборки по коду исхода."""
    return group_by(cases, lambda c: c.ishod)


def prepare(cases, has_fact) -> StatView:
    """Считает все группировки отчёта по списку случаев (omsreg.utils…stat_common.Case).

    has_fact — есть ли в файле поле FACT (без него койко-дни не считаются).
    """
    by_type = {}
    for st, cs in group_by(cases, lambda c: c.st_type).items():
        by_type[st] = TypeStat(
            cases=cs,
            total=_total(cs),
            kotds=sorted({c.kotd for c in cs if c.kotd is not None}),
            by_kotd=group_by(cs, lambda c: c.kotd),
            by_kmkb=group_by(cs, lambda c: c.kmkb),
        )
    return StatView(
        cases=cases,
        n=len(cases),
        total_sum=_total(cases),
        total_fact=sum(c.fact for c in cases if c.fact) if has_fact else 0,
        by_type=by_type,
        by_kotd=group_by(cases, lambda c: (c.st_type, c.kotd)),
        by_ishod=_by_ishod(cases),
    )


def _outcome_stats(items) -> list:
    """Список OutcomeStat по парам (код исхода, случаи) в заданном порядке."""
    return [OutcomeStat(ish, len(cs), _total(cs), min(_costs(cs)), max(_costs(cs)))
            for ish, cs in items]


def _outcomes_by_count(by_ish: dict) -> list:
    """Исходы по убыванию числа случаев — порядок разделов «по исходам»."""
    return _outcome_stats(sorted(by_ish.items(), key=lambda kv: -len(kv[1])))


def _outcomes_by_code(by_ish: dict) -> list:
    """Исходы по коду (не указанный — последним) — порядок строк внутри диагноза."""
    return _outcome_stats(sorted(by_ish.items(), key=lambda kv: (kv[0] is None, kv[0])))


def _diag_order(t: TypeStat) -> list:
    """Диагнозы (KMKB) типа стационара по убыванию суммы STOIM."""
    return sorted(t.by_kmkb.items(), key=lambda kv: -_total(kv[1]))


def _type_cases(view: StatView, st: str) -> tuple:
    """(случаи, сумма STOIM) по типу стационара; ([], 0), если случаев этого типа нет."""
    t = view.by_type.get(st)
    return (t.cases, t.total) if t else ([], 0)


def _day_list(day_kotd) -> str:
    """Коды отделений дневного стационара по возрастанию, через запятую."""
    return ", ".join(str(k) for k in sorted(day_kotd))


# --- текстовая форма: по функции на раздел, каждая возвращает список строк ---

def _txt_head(dbf_path, deleted, day_kotd, total_in_file) -> list:
    """Шапка отчёта: файл, время, сколько записей и где граница дневного стационара."""
    return [
        _RULE,
        f"СТАТИСТИКА ПО ФАЙЛУ: {dbf_path}",
        f"Сформировано: {report_stamp()}",
        f"Записей в файле: {total_in_file}"
        + (f", помечено удалёнными и исключено: {deleted}" if deleted else ""),
        f"Дневной стационар — коды отделений (KOTD): {_day_list(day_kotd)}; "
        f"круглосуточный — все остальные",
        _RULE,
    ]


def _txt_summary(view: StatView, has_fact) -> list:
    """Раздел «общая сводка»: случаи, сумма, средняя, койко-дни."""
    n, total = view.n, view.total_sum
    out = ["",
           "ОБЩАЯ СВОДКА",
           f"  случаев:                  {n}",
           f"  сумма STOIM:              {money(total)}",
           f"  средняя стоимость случая: {money(total / n) if n else '-'}"]
    if has_fact:
        fact = view.total_fact
        out.append(f"  койко-дней (FACT):        {fact:.0f}"
                   + (f", в среднем {fact / n:.1f} на случай" if n else "")
                   + (f", средняя стоимость койко-дня {money(total / fact)}" if fact else ""))
    return out


def _txt_types(view: StatView) -> list:
    """Раздел «по типам стационара»: дневной и круглосуточный рядом."""
    out = ["",
           "ПО ТИПАМ СТАЦИОНАРА",
           f"  {'тип':<28} {'случаев':>8} {'доля':>7} {'сумма STOIM':>18} {'доля':>7} {'средняя':>14}"]
    for st in (DAY_TYPE, ROUND_TYPE):
        cs, s = _type_cases(view, st)
        out.append(f"  {st:<28} {len(cs):>8} {pct(len(cs), view.n):>7} {money(s):>18} "
                   f"{pct(s, view.total_sum):>7} {money(s / len(cs)) if cs else '-':>14}")
    return out


def _txt_departments(view: StatView, names) -> list:
    """Раздел «по отделениям (KOTD)»: отделения внутри типов стационара."""
    out = ["",
           "ПО ОТДЕЛЕНИЯМ (KOTD)",
           f"  {'отделение':<28} {'тип':<26} {'случаев':>8} {'сумма STOIM':>18} {'средняя':>14}"]
    for (st, kotd), cs in sorted(view.by_kotd.items(), key=lambda kv: (kv[0][0], kv[0][1] or 0)):
        s = _total(cs)
        out.append(f"  {kotd_name(kotd, names):<28} {st:<26} {len(cs):>8} "
                   f"{money(s):>18} {money(s / len(cs)):>14}")
    return out


def _txt_outcomes(view: StatView) -> list:
    """Раздел «по исходам (весь файл)»."""
    out = ["",
           "ПО ИСХОДАМ (весь файл)",
           f"  {'исход':<40} {'случаев':>8} {'доля':>7} {'сумма STOIM':>18}"
           f" {'мин. случай':>14} {'макс. случай':>14}"]
    out.extend(f"  {ishod_name(o.ishod):<40} {o.count:>8} {pct(o.count, view.n):>7}"
               f" {money(o.total):>18} {money(o.low):>14} {money(o.high):>14}"
               for o in _outcomes_by_count(view.by_ishod))
    return out


def _txt_outcome_rows(stats, subset_n, indent) -> list:
    """Строки «исход …» внутри выборки: доля считается от размера выборки."""
    return [f"{indent}{ishod_name(o.ishod):<40} {o.count:>5} {pct(o.count, subset_n):>7} "
            f"{money(o.total):>16}  мин {money(o.low):>12}  макс {money(o.high):>12}"
            for o in stats]


def _txt_outcomes_by_dept(view: StatView, names) -> list:
    """Раздел «исходы по стационарам и отделениям»."""
    out = ["", _RULE, "ИСХОДЫ ПО СТАЦИОНАРАМ И ОТДЕЛЕНИЯМ", _RULE]
    for st in (DAY_TYPE, ROUND_TYPE):
        t = view.by_type.get(st)
        if t is None:
            continue
        out += ["",
                f"{st.upper()} — случаев {len(t.cases)}, сумма {money(t.total)}",
                "  по исходам (весь стационар):"]
        out += _txt_outcome_rows(_outcomes_by_count(_by_ishod(t.cases)), len(t.cases), "    ")
        for kotd in sorted(t.by_kotd, key=lambda k: (k is None, k)):
            cs_k = t.by_kotd[kotd]
            out.append(f"  отделение {kotd_name(kotd, names)} — случаев {len(cs_k)}, "
                       f"сумма {money(_total(cs_k))}:")
            out += _txt_outcome_rows(_outcomes_by_count(_by_ishod(cs_k)), len(cs_k), "      ")
    return out


def _txt_type_table(st: str, t: TypeStat) -> list:
    """Главная таблица одного типа стационара: KMKB -> исходы, с итогом."""
    out = ["", _RULE,
           f"{st.upper()}  (KOTD: {', '.join(map(str, t.kotds))})  —  "
           f"случаев: {len(t.cases)}, сумма: {money(t.total)}, "
           f"средняя: {money(t.total / len(t.cases))}",
           _RULE,
           f"{'KMKB':<12} {'случаев':>8} {'сумма STOIM':>18} {'средняя':>14} {'% суммы':>8}",
           _TABLE_RULE]
    for kmkb, cs in _diag_order(t):
        s = _total(cs)
        out.append(f"{kmkb:<12} {len(cs):>8} {money(s):>18} {money(s / len(cs)):>14} "
                   f"{pct(s, t.total):>8}")
        out += [f"    исход {ishod_name(o.ishod):<42} {o.count:>5} {money(o.total):>18}"
                f"  мин {money(o.low):>13}  макс {money(o.high):>13}"
                for o in _outcomes_by_code(_by_ishod(cs))]
    out += [_TABLE_RULE,
            f"{'ИТОГО':<12} {len(t.cases):>8} {money(t.total):>18} "
            f"{money(t.total / len(t.cases)):>14} {'100.0%':>8}"]
    return out


def _txt_main_tables(view: StatView) -> list:
    """Главные таблицы: стационар -> KMKB -> исход, по разделу на тип стационара."""
    out = []
    for st in (DAY_TYPE, ROUND_TYPE):
        t = view.by_type.get(st)
        if t is None:
            out += ["", f"{st.upper()}: случаев нет"]
            continue
        out += _txt_type_table(st, t)
    return out


def _txt_total(view: StatView) -> list:
    """Итоговая строка отчёта."""
    return ["", _RULE, f"ВСЕГО: случаев {view.n}, сумма {money(view.total_sum)}"]


def _csv_rows(view: StatView) -> list:
    """Таблица для CSV: по строке на диагноз, на исход внутри диагноза и итоги."""
    rows = [list(_CSV_HEADER)]
    for st in (DAY_TYPE, ROUND_TYPE):
        t = view.by_type.get(st)
        if t is None:
            continue
        for kmkb, cs in _diag_order(t):
            s, costs = _total(cs), _costs(cs)
            rows.append([st, kmkb, "", "итого по диагнозу", len(cs),
                         csv_num(s), csv_num(s / len(cs)),
                         csv_num(min(costs)), csv_num(max(costs))])
            rows += [[st, kmkb, "" if o.ishod is None else o.ishod, ishod_name(o.ishod),
                      o.count, csv_num(o.total), csv_num(o.total / o.count),
                      csv_num(o.low), csv_num(o.high)]
                     for o in _outcomes_by_code(_by_ishod(cs))]
        costs_t = _costs(t.cases)
        rows.append([st, "ИТОГО", "", "", len(t.cases),
                     csv_num(t.total), csv_num(t.total / len(t.cases)),
                     csv_num(min(costs_t)), csv_num(max(costs_t))])
    n, total, all_costs = view.n, view.total_sum, _costs(view.cases)
    rows.append(["ВСЕГО", "", "", "", n, csv_num(total) if n else "",
                 csv_num(total / n) if n else "",
                 csv_num(min(all_costs)) if n else "", csv_num(max(all_costs)) if n else ""])
    return rows


def build_report(dbf_path, cases, deleted, has_fact, day_kotd, total_in_file, names=None):
    """Строит текстовый отчёт (список строк) и данные для CSV (список списков).

    names — словарь названий отделений {код: название}; None -> встроенный KOTD_NAMES.
    """
    view = prepare(cases, has_fact)
    out = _txt_head(dbf_path, deleted, day_kotd, total_in_file)
    out += _txt_summary(view, has_fact)
    out += _txt_types(view)
    out += _txt_departments(view, names)
    out += _txt_outcomes(view)
    out += _txt_outcomes_by_dept(view, names)
    out += _txt_main_tables(view)
    out += _txt_total(view)
    return out, _csv_rows(view)


# --- HTML-форма: те же разделы, каждая функция возвращает куски разметки ---

def _bar_cell(share_of_max, text) -> str:
    """Числовая ячейка таблицы с полосой величины (разметка — core.report_html.bar)."""
    return f'<td class="num">{bar(share_of_max, text)}</td>'


def _html_head(dbf_path, deleted, day_kotd, total_in_file) -> list:
    """Заголовок и строка-подпись HTML-отчёта."""
    e = html.escape
    return [
        f"<h1>Статистика по файлу {e(str(dbf_path.name))}</h1>",
        f'<div class="meta">Сформировано {report_stamp(seconds=False)} · '
        f'записей в файле: {total_in_file}'
        + (f' (помечено удалёнными и исключено: {deleted})' if deleted else '')
        + f' · дневной стационар — KOTD: {e(_day_list(day_kotd))}, круглосуточный — все остальные</div>',
    ]


def _html_tiles(view: StatView, has_fact) -> list:
    """Карточки с главными цифрами (аналог «общей сводки» текстовой формы)."""
    n, total, fact = view.n, view.total_sum, view.total_fact
    out = ['<div class="tiles">', tile(n, "случаев"), tile(money(total), "сумма STOIM")]
    if n:
        out.append(tile(money(total / n), "средняя стоимость случая"))
    if has_fact and fact:
        out.append(tile(f"{fact:.0f}", f"койко-дней (FACT), в среднем {fact / n:.1f} на случай"))
        out.append(tile(money(total / fact), "средняя стоимость койко-дня"))
    out.append('</div>')
    return out


def _html_types(view: StatView) -> list:
    """Таблица «по типам стационара»."""
    e = html.escape
    out = ['<h2>По типам стационара</h2><table class="plain"><thead><tr>'
           '<th>тип</th><th class="num">случаев</th><th class="num">доля случаев</th>'
           '<th class="num">средняя</th><th class="num">сумма STOIM (доля)</th></tr></thead><tbody>']
    max_sum = max((t.total for t in view.by_type.values()), default=0)
    for st in (DAY_TYPE, ROUND_TYPE):
        cs, s = _type_cases(view, st)
        out.append(f'<tr><td>{e(st)}</td><td class="num">{len(cs)}</td>'
                   f'<td class="num">{pct(len(cs), view.n)}</td>'
                   f'<td class="num">{money(s / len(cs)) if cs else "-"}</td>'
                   + _bar_cell(s / max_sum if max_sum else 0,
                          f"{money(s)} ({pct(s, view.total_sum).strip()})")
                   + '</tr>')
    out.append('</tbody></table>')
    return out


def _html_departments(view: StatView, names) -> list:
    """Таблица «по отделениям (KOTD)» — по убыванию суммы STOIM."""
    e = html.escape
    out = ['<h2>По отделениям (KOTD)</h2><table class="plain"><thead><tr>'
           '<th>отделение</th><th>тип</th><th class="num">случаев</th>'
           '<th class="num">средняя</th><th class="num">сумма STOIM (доля)</th></tr></thead><tbody>']
    max_sum = max((_total(cs) for cs in view.by_kotd.values()), default=0)
    for (st, kotd), cs in sorted(view.by_kotd.items(), key=lambda kv: -_total(kv[1])):
        s = _total(cs)
        out.append(f'<tr><td>{e(kotd_name(kotd, names))}</td><td>{e(st)}</td>'
                   f'<td class="num">{len(cs)}</td><td class="num">{money(s / len(cs))}</td>'
                   + _bar_cell(s / max_sum if max_sum else 0,
                          f"{money(s)} ({pct(s, view.total_sum).strip()})")
                   + '</tr>')
    out.append('</tbody></table>')
    return out


def _html_outcomes(view: StatView) -> list:
    """Таблица «по исходам (весь файл)»."""
    e = html.escape
    out = ['<h2>По исходам (весь файл)</h2><table class="plain"><thead><tr>'
           '<th>исход</th><th class="num">случаев</th><th class="num">доля</th>'
           '<th class="num">мин. случай</th><th class="num">макс. случай</th>'
           '<th class="num">сумма STOIM (доля)</th></tr></thead><tbody>']
    max_sum = max((_total(cs) for cs in view.by_ishod.values()), default=0)
    out.extend(f'<tr><td>{e(ishod_name(o.ishod))}</td><td class="num">{o.count}</td>'
               f'<td class="num">{pct(o.count, view.n)}</td>'
               f'<td class="num">{money(o.low)}</td><td class="num">{money(o.high)}</td>'
               + _bar_cell(o.total / max_sum if max_sum else 0,
                      f"{money(o.total)} ({pct(o.total, view.total_sum).strip()})")
               + '</tr>'
               for o in _outcomes_by_count(view.by_ishod))
    out.append('</tbody></table>')
    return out


def _html_outcome_rows(stats, subset_n, css) -> list:
    """Строки «исход …» внутри выборки: доля считается от размера выборки."""
    e = html.escape
    return [f'<tr class="{css}"><td>исход {e(ishod_name(o.ishod))}</td>'
            f'<td class="num">{o.count}</td><td class="num">{pct(o.count, subset_n)}</td>'
            f'<td class="num">{money(o.low)}</td><td class="num">{money(o.high)}</td>'
            f'<td class="num">{money(o.total)}</td></tr>' for o in stats]


def _html_dept_table(st: str, t: TypeStat, names) -> list:
    """Таблица «исходы по отделениям» для одного типа стационара."""
    e = html.escape
    out = [f'<h2>Исходы по отделениям — {e(st)} '
           f'(случаев: {len(t.cases)}, сумма: {money(t.total)})</h2>',
           '<table class="main"><thead><tr><th>отделение / исход</th>'
           '<th class="num">случаев</th><th class="num">доля</th>'
           '<th class="num">мин. случай</th><th class="num">макс. случай</th>'
           '<th class="num">сумма STOIM</th></tr></thead><tbody>',
           f'<tr class="total"><td>Весь стационар</td><td class="num">{len(t.cases)}</td>'
           f'<td class="num">100.0%</td><td></td><td></td>'
           f'<td class="num">{money(t.total)}</td></tr>']
    out += _html_outcome_rows(_outcomes_by_count(_by_ishod(t.cases)), len(t.cases), "ishod")
    for kotd in sorted(t.by_kotd, key=lambda k: (k is None, k)):
        cs_k = t.by_kotd[kotd]
        out.append(f'<tr class="diag"><td><b>Отделение {e(kotd_name(kotd, names))}</b></td>'
                   f'<td class="num">{len(cs_k)}</td>'
                   f'<td class="num">{pct(len(cs_k), len(t.cases))}</td>'
                   f'<td></td><td></td><td class="num">{money(_total(cs_k))}</td></tr>')
        out += _html_outcome_rows(_outcomes_by_count(_by_ishod(cs_k)), len(cs_k), "ishod")
    out.append('</tbody></table>')
    return out


def _html_outcomes_by_dept(view: StatView, names) -> list:
    """Раздел «исходы по отделениям» — по таблице на непустой тип стационара."""
    out = []
    for st in (DAY_TYPE, ROUND_TYPE):
        t = view.by_type.get(st)
        if t is not None:
            out += _html_dept_table(st, t, names)
    return out


def _html_type_table(st: str, t: TypeStat) -> list:
    """Главная таблица одного типа стационара: KMKB -> исходы, с итогом."""
    e = html.escape
    out = [f'<h2>{e(st)} (KOTD: {e(", ".join(map(str, t.kotds)))}) — случаев: {len(t.cases)}, '
           f'сумма: {money(t.total)}, средняя: {money(t.total / len(t.cases))}</h2>',
           '<table class="main"><thead><tr>'
           '<th>КМКБ / исход</th><th class="num">случаев</th><th class="num">средняя</th>'
           '<th class="num">мин. случай</th><th class="num">макс. случай</th>'
           '<th class="num">сумма STOIM (% от стационара)</th></tr></thead><tbody>']
    max_diag = max((_total(cs) for cs in t.by_kmkb.values()), default=0)
    for kmkb, cs in _diag_order(t):
        s, costs = _total(cs), _costs(cs)
        key = e(kmkb.upper())
        out.append(f'<tr class="diag" data-kmkb="{key}"><td><b>{e(kmkb)}</b></td>'
                   f'<td class="num">{len(cs)}</td><td class="num">{money(s / len(cs))}</td>'
                   f'<td class="num">{money(min(costs))}</td>'
                   f'<td class="num">{money(max(costs))}</td>'
                   + _bar_cell(s / max_diag if max_diag else 0,
                          f"{money(s)} ({pct(s, t.total).strip()})")
                   + '</tr>')
        out += [f'<tr class="ishod" data-kmkb="{key}"><td>исход {e(ishod_name(o.ishod))}</td>'
                f'<td class="num">{o.count}</td><td class="num">{money(o.total / o.count)}</td>'
                f'<td class="num">{money(o.low)}</td><td class="num">{money(o.high)}</td>'
                f'<td class="num">{money(o.total)}</td></tr>'
                for o in _outcomes_by_code(_by_ishod(cs))]
    out += [f'<tr class="total"><td>ИТОГО</td><td class="num">{len(t.cases)}</td>'
            f'<td class="num">{money(t.total / len(t.cases))}</td><td></td><td></td>'
            f'<td class="num">{money(t.total)}</td></tr>',
            '</tbody></table>']
    return out


def _html_main_tables(view: StatView) -> list:
    """Главные таблицы HTML: панель фильтра и по таблице на тип стационара."""
    e = html.escape
    out = ['<div class="controls"><input id="flt" type="search" placeholder="фильтр по коду МКБ…">'
           '<button id="tgl" type="button">Скрыть/показать исходы</button></div>']
    for st in (DAY_TYPE, ROUND_TYPE):
        t = view.by_type.get(st)
        if t is None:
            out.append(f"<h2>{e(st)}</h2><p>случаев нет</p>")
            continue
        out += _html_type_table(st, t)
    return out


def build_html(dbf_path, cases, deleted, has_fact, day_kotd, total_in_file, names=None) -> str:
    """Строит самодостаточный HTML-отчёт (строка).

    Цифры те же, что в текстовом отчёте: обе формы читают один и тот же StatView.
    names — словарь названий отделений {код: название}; None -> встроенный KOTD_NAMES.
    """
    view = prepare(cases, has_fact)
    parts = _html_head(dbf_path, deleted, day_kotd, total_in_file)
    parts += _html_tiles(view, has_fact)
    parts += _html_types(view)
    parts += _html_departments(view, names)
    parts += _html_outcomes(view)
    parts += _html_outcomes_by_dept(view, names)
    parts += _html_main_tables(view)
    parts.append(f'<h2>Всего: случаев {view.n}, сумма {money(view.total_sum)}</h2>')
    return page(f"Статистика {html.escape(dbf_path.name)}", "\n".join(parts),
                extra_css=_STAT_CSS, script=_STAT_SCRIPT)
