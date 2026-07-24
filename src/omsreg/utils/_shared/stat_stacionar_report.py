"""Текстовый (+CSV) и HTML-отчёт статистики стационара.

Обе формы строятся из одного списка случаев и показывают одни и те же цифры:
разбивка по типам стационара, отделениям, исходам и таблицы стационар -> МКБ ->
исход. HTML самодостаточен (тема из core.report_html + свои правила ниже).
"""

from __future__ import annotations

import html
from collections import defaultdict
from datetime import datetime

from omsreg.core import csv_num, money, pct
from omsreg.core.report_html import page, tile
from omsreg.utils._shared.stat_common import DAY_TYPE, ROUND_TYPE, ishod_name, kotd_name

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


def build_report(dbf_path, cases, deleted, has_fact, day_kotd, total_in_file, names=None):
    """Строит текстовый отчёт (список строк) и данные для CSV (список списков).
    names — словарь названий отделений {код: название}; None -> встроенный KOTD_NAMES."""
    out = []
    csv_rows = [["тип стационара", "KMKB", "код исхода", "исход",
                 "случаев", "сумма STOIM", "средняя стоимость",
                 "мин. стоимость случая", "макс. стоимость случая"]]
    w = out.append

    n = len(cases)
    total_sum = sum(c[4] for c in cases)
    total_fact = sum(c[5] for c in cases if c[5]) if has_fact else 0

    w("=" * 100)
    w(f"СТАТИСТИКА ПО ФАЙЛУ: {dbf_path}")
    w(f"Сформировано: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")
    w(f"Записей в файле: {total_in_file}"
      + (f", помечено удалёнными и исключено: {deleted}" if deleted else ""))
    w(f"Дневной стационар — коды отделений (KOTD): {', '.join(str(k) for k in sorted(day_kotd))}; "
      f"круглосуточный — все остальные")
    w("=" * 100)

    # ---------- общая сводка ----------
    w("")
    w("ОБЩАЯ СВОДКА")
    w(f"  случаев:                  {n}")
    w(f"  сумма STOIM:              {money(total_sum)}")
    w(f"  средняя стоимость случая: {money(total_sum / n) if n else '-'}")
    if has_fact:
        w(f"  койко-дней (FACT):        {total_fact:.0f}"
          + (f", в среднем {total_fact / n:.1f} на случай" if n else "")
          + (f", средняя стоимость койко-дня {money(total_sum / total_fact)}" if total_fact else ""))

    # ---------- по типам стационара ----------
    by_type = defaultdict(list)
    for c in cases:
        by_type[c[0]].append(c)
    w("")
    w("ПО ТИПАМ СТАЦИОНАРА")
    w(f"  {'тип':<28} {'случаев':>8} {'доля':>7} {'сумма STOIM':>18} {'доля':>7} {'средняя':>14}")
    for st in (DAY_TYPE, ROUND_TYPE):
        cs = by_type.get(st, [])
        s = sum(c[4] for c in cs)
        w(f"  {st:<28} {len(cs):>8} {pct(len(cs), n):>7} {money(s):>18} "
          f"{pct(s, total_sum):>7} {money(s / len(cs)) if cs else '-':>14}")

    # ---------- по отделениям ----------
    by_kotd = defaultdict(list)
    for c in cases:
        by_kotd[(c[0], c[1])].append(c)
    w("")
    w("ПО ОТДЕЛЕНИЯМ (KOTD)")
    w(f"  {'отделение':<28} {'тип':<26} {'случаев':>8} {'сумма STOIM':>18} {'средняя':>14}")
    for (st, kotd), cs in sorted(by_kotd.items(), key=lambda kv: (kv[0][0], kv[0][1] or 0)):
        s = sum(c[4] for c in cs)
        w(f"  {kotd_name(kotd, names):<28} {st:<26} {len(cs):>8} "
          f"{money(s):>18} {money(s / len(cs)):>14}")

    # ---------- по исходам (весь файл) ----------
    by_ishod = defaultdict(list)
    for c in cases:
        by_ishod[c[3]].append(c)
    w("")
    w("ПО ИСХОДАМ (весь файл)")
    w(f"  {'исход':<40} {'случаев':>8} {'доля':>7} {'сумма STOIM':>18}"
      f" {'мин. случай':>14} {'макс. случай':>14}")
    for ish, cs in sorted(by_ishod.items(), key=lambda kv: -len(kv[1])):
        s = sum(c[4] for c in cs)
        costs = [c[4] for c in cs]
        w(f"  {ishod_name(ish):<40} {len(cs):>8} {pct(len(cs), n):>7} {money(s):>18}"
          f" {money(min(costs)):>14} {money(max(costs)):>14}")

    # ---------- исходы по стационарам и отделениям ----------
    def _outcomes(subset, indent):
        by_ish = defaultdict(list)
        for c in subset:
            by_ish[c[3]].append(c)
        for ish, cs in sorted(by_ish.items(), key=lambda kv: -len(kv[1])):
            s = sum(c[4] for c in cs)
            costs = [c[4] for c in cs]
            w(f"{indent}{ishod_name(ish):<40} {len(cs):>5} {pct(len(cs), len(subset)):>7} "
              f"{money(s):>16}  мин {money(min(costs)):>12}  макс {money(max(costs)):>12}")

    w("")
    w("=" * 100)
    w("ИСХОДЫ ПО СТАЦИОНАРАМ И ОТДЕЛЕНИЯМ")
    w("=" * 100)
    for st in (DAY_TYPE, ROUND_TYPE):
        cs_type = by_type.get(st, [])
        if not cs_type:
            continue
        w("")
        w(f"{st.upper()} — случаев {len(cs_type)}, сумма {money(sum(c[4] for c in cs_type))}")
        w("  по исходам (весь стационар):")
        _outcomes(cs_type, "    ")
        by_kotd_t = defaultdict(list)
        for c in cs_type:
            by_kotd_t[c[1]].append(c)
        for kotd in sorted(by_kotd_t, key=lambda k: (k is None, k)):
            cs_k = by_kotd_t[kotd]
            w(f"  отделение {kotd_name(kotd, names)} — случаев {len(cs_k)}, "
              f"сумма {money(sum(c[4] for c in cs_k))}:")
            _outcomes(cs_k, "      ")

    # ---------- главные таблицы: стационар -> KMKB -> ISHOD ----------
    for st in (DAY_TYPE, ROUND_TYPE):
        cs_type = by_type.get(st, [])
        if not cs_type:
            w("")
            w(f"{st.upper()}: случаев нет")
            continue
        type_sum = sum(c[4] for c in cs_type)
        kotds = sorted({c[1] for c in cs_type if c[1] is not None})
        w("")
        w("=" * 100)
        w(f"{st.upper()}  (KOTD: {', '.join(map(str, kotds))})  —  "
          f"случаев: {len(cs_type)}, сумма: {money(type_sum)}, "
          f"средняя: {money(type_sum / len(cs_type))}")
        w("=" * 100)
        w(f"{'KMKB':<12} {'случаев':>8} {'сумма STOIM':>18} {'средняя':>14} {'% суммы':>8}")
        w("-" * 64)

        by_kmkb = defaultdict(list)
        for c in cs_type:
            by_kmkb[c[2]].append(c)

        for kmkb, cs in sorted(by_kmkb.items(), key=lambda kv: -sum(c[4] for c in kv[1])):
            s = sum(c[4] for c in cs)
            costs_d = [c[4] for c in cs]
            w(f"{kmkb:<12} {len(cs):>8} {money(s):>18} {money(s / len(cs)):>14} {pct(s, type_sum):>8}")
            csv_rows.append([st, kmkb, "", "итого по диагнозу", len(cs),
                             csv_num(s), csv_num(s / len(cs)),
                             csv_num(min(costs_d)), csv_num(max(costs_d))])
            by_ish = defaultdict(list)
            for c in cs:
                by_ish[c[3]].append(c)
            for ish, cs_i in sorted(by_ish.items(), key=lambda kv: (kv[0] is None, kv[0])):
                s_i = sum(c[4] for c in cs_i)
                costs = [c[4] for c in cs_i]
                w(f"    исход {ishod_name(ish):<42} {len(cs_i):>5} {money(s_i):>18}"
                  f"  мин {money(min(costs)):>13}  макс {money(max(costs)):>13}")
                csv_rows.append([st, kmkb, "" if ish is None else ish, ishod_name(ish),
                                 len(cs_i), csv_num(s_i), csv_num(s_i / len(cs_i)),
                                 csv_num(min(costs)), csv_num(max(costs))])
        w("-" * 64)
        w(f"{'ИТОГО':<12} {len(cs_type):>8} {money(type_sum):>18} "
          f"{money(type_sum / len(cs_type)):>14} {'100.0%':>8}")
        costs_t = [c[4] for c in cs_type]
        csv_rows.append([st, "ИТОГО", "", "", len(cs_type),
                         csv_num(type_sum), csv_num(type_sum / len(cs_type)),
                         csv_num(min(costs_t)), csv_num(max(costs_t))])

    w("")
    w("=" * 100)
    w(f"ВСЕГО: случаев {n}, сумма {money(total_sum)}")
    all_costs = [c[4] for c in cases]
    csv_rows.append(["ВСЕГО", "", "", "", n, csv_num(total_sum) if n else "",
                     csv_num(total_sum / n) if n else "",
                     csv_num(min(all_costs)) if n else "", csv_num(max(all_costs)) if n else ""])
    return out, csv_rows


def build_html(dbf_path, cases, deleted, has_fact, day_kotd, total_in_file, names=None) -> str:
    """Строит самодостаточный HTML-отчёт (строка). Цифры те же, что в текстовом отчёте.
    names — словарь названий отделений {код: название}; None -> встроенный KOTD_NAMES."""
    e = html.escape
    n = len(cases)
    total_sum = sum(c[4] for c in cases)
    total_fact = sum(c[5] for c in cases if c[5]) if has_fact else 0

    by_type = defaultdict(list)
    for c in cases:
        by_type[c[0]].append(c)

    def bar(share_of_max, text):
        """Ячейка с полосой величины: ширина — доля от максимума строки-лидера."""
        w = max(0.0, min(1.0, share_of_max)) * 100
        return (f'<td class="num"><div class="barwrap"><div class="bar" '
                f'style="width:{w:.1f}%"></div><span>{text}</span></div></td>')

    parts = []
    p = parts.append

    day_list = ", ".join(str(k) for k in sorted(day_kotd))
    p(f"<h1>Статистика по файлу {e(str(dbf_path.name))}</h1>")
    p(f'<div class="meta">Сформировано {datetime.now().strftime("%d.%m.%Y %H:%M")} · '
      f'записей в файле: {total_in_file}'
      + (f' (помечено удалёнными и исключено: {deleted})' if deleted else '')
      + f' · дневной стационар — KOTD: {e(day_list)}, круглосуточный — все остальные</div>')

    # ---------- карточки ----------
    p('<div class="tiles">')
    p(tile(n, "случаев"))
    p(tile(money(total_sum), "сумма STOIM"))
    if n:
        p(tile(money(total_sum / n), "средняя стоимость случая"))
    if has_fact and total_fact:
        p(tile(f"{total_fact:.0f}",
               f"койко-дней (FACT), в среднем {total_fact / n:.1f} на случай"))
        p(tile(money(total_sum / total_fact), "средняя стоимость койко-дня"))
    p('</div>')

    # ---------- по типам стационара ----------
    p('<h2>По типам стационара</h2><table class="plain"><thead><tr>'
      '<th>тип</th><th class="num">случаев</th><th class="num">доля случаев</th>'
      '<th class="num">средняя</th><th class="num">сумма STOIM (доля)</th></tr></thead><tbody>')
    max_sum = max((sum(c[4] for c in cs) for cs in by_type.values()), default=0)
    for st in (DAY_TYPE, ROUND_TYPE):
        cs = by_type.get(st, [])
        s = sum(c[4] for c in cs)
        p(f'<tr><td>{e(st)}</td><td class="num">{len(cs)}</td>'
          f'<td class="num">{pct(len(cs), n)}</td>'
          f'<td class="num">{money(s / len(cs)) if cs else "-"}</td>'
          + bar(s / max_sum if max_sum else 0, f"{money(s)} ({pct(s, total_sum).strip()})")
          + '</tr>')
    p('</tbody></table>')

    # ---------- по отделениям ----------
    by_kotd = defaultdict(list)
    for c in cases:
        by_kotd[(c[0], c[1])].append(c)
    p('<h2>По отделениям (KOTD)</h2><table class="plain"><thead><tr>'
      '<th>отделение</th><th>тип</th><th class="num">случаев</th>'
      '<th class="num">средняя</th><th class="num">сумма STOIM (доля)</th></tr></thead><tbody>')
    max_sum = max((sum(c[4] for c in cs) for cs in by_kotd.values()), default=0)
    for (st, kotd), cs in sorted(by_kotd.items(), key=lambda kv: -sum(c[4] for c in kv[1])):
        s = sum(c[4] for c in cs)
        p(f'<tr><td>{e(kotd_name(kotd, names))}</td><td>{e(st)}</td>'
          f'<td class="num">{len(cs)}</td><td class="num">{money(s / len(cs))}</td>'
          + bar(s / max_sum if max_sum else 0, f"{money(s)} ({pct(s, total_sum).strip()})")
          + '</tr>')
    p('</tbody></table>')

    # ---------- по исходам ----------
    by_ishod = defaultdict(list)
    for c in cases:
        by_ishod[c[3]].append(c)
    p('<h2>По исходам (весь файл)</h2><table class="plain"><thead><tr>'
      '<th>исход</th><th class="num">случаев</th><th class="num">доля</th>'
      '<th class="num">мин. случай</th><th class="num">макс. случай</th>'
      '<th class="num">сумма STOIM (доля)</th></tr></thead><tbody>')
    max_sum = max((sum(c[4] for c in cs) for cs in by_ishod.values()), default=0)
    for ish, cs in sorted(by_ishod.items(), key=lambda kv: -len(kv[1])):
        s = sum(c[4] for c in cs)
        costs = [c[4] for c in cs]
        p(f'<tr><td>{e(ishod_name(ish))}</td><td class="num">{len(cs)}</td>'
          f'<td class="num">{pct(len(cs), n)}</td>'
          f'<td class="num">{money(min(costs))}</td><td class="num">{money(max(costs))}</td>'
          + bar(s / max_sum if max_sum else 0, f"{money(s)} ({pct(s, total_sum).strip()})")
          + '</tr>')
    p('</tbody></table>')

    # ---------- исходы по стационарам и отделениям ----------
    def outcome_rows(subset, css):
        by_ish = defaultdict(list)
        for c in subset:
            by_ish[c[3]].append(c)
        for ish, cs in sorted(by_ish.items(), key=lambda kv: -len(kv[1])):
            s = sum(c[4] for c in cs)
            costs = [c[4] for c in cs]
            p(f'<tr class="{css}"><td>исход {e(ishod_name(ish))}</td>'
              f'<td class="num">{len(cs)}</td><td class="num">{pct(len(cs), len(subset))}</td>'
              f'<td class="num">{money(min(costs))}</td><td class="num">{money(max(costs))}</td>'
              f'<td class="num">{money(s)}</td></tr>')

    for st in (DAY_TYPE, ROUND_TYPE):
        cs_type = by_type.get(st, [])
        if not cs_type:
            continue
        type_sum = sum(c[4] for c in cs_type)
        p(f'<h2>Исходы по отделениям — {e(st)} (случаев: {len(cs_type)}, сумма: {money(type_sum)})</h2>')
        p('<table class="main"><thead><tr><th>отделение / исход</th>'
          '<th class="num">случаев</th><th class="num">доля</th>'
          '<th class="num">мин. случай</th><th class="num">макс. случай</th>'
          '<th class="num">сумма STOIM</th></tr></thead><tbody>')
        p(f'<tr class="total"><td>Весь стационар</td><td class="num">{len(cs_type)}</td>'
          f'<td class="num">100.0%</td><td></td><td></td>'
          f'<td class="num">{money(type_sum)}</td></tr>')
        outcome_rows(cs_type, "ishod")
        by_kotd_t = defaultdict(list)
        for c in cs_type:
            by_kotd_t[c[1]].append(c)
        for kotd in sorted(by_kotd_t, key=lambda k: (k is None, k)):
            cs_k = by_kotd_t[kotd]
            s_k = sum(c[4] for c in cs_k)
            p(f'<tr class="diag"><td><b>Отделение {e(kotd_name(kotd, names))}</b></td>'
              f'<td class="num">{len(cs_k)}</td><td class="num">{pct(len(cs_k), len(cs_type))}</td>'
              f'<td></td><td></td><td class="num">{money(s_k)}</td></tr>')
            outcome_rows(cs_k, "ishod")
        p('</tbody></table>')

    # ---------- главные таблицы ----------
    p('<div class="controls"><input id="flt" type="search" placeholder="фильтр по коду МКБ…">'
      '<button id="tgl" type="button">Скрыть/показать исходы</button></div>')
    for st in (DAY_TYPE, ROUND_TYPE):
        cs_type = by_type.get(st, [])
        if not cs_type:
            p(f"<h2>{e(st)}</h2><p>случаев нет</p>")
            continue
        type_sum = sum(c[4] for c in cs_type)
        kotds = ", ".join(map(str, sorted({c[1] for c in cs_type if c[1] is not None})))
        p(f'<h2>{e(st)} (KOTD: {e(kotds)}) — случаев: {len(cs_type)}, '
          f'сумма: {money(type_sum)}, средняя: {money(type_sum / len(cs_type))}</h2>')
        p('<table class="main"><thead><tr>'
          '<th>КМКБ / исход</th><th class="num">случаев</th><th class="num">средняя</th>'
          '<th class="num">мин. случай</th><th class="num">макс. случай</th>'
          '<th class="num">сумма STOIM (% от стационара)</th></tr></thead><tbody>')
        by_kmkb = defaultdict(list)
        for c in cs_type:
            by_kmkb[c[2]].append(c)
        max_diag = max((sum(c[4] for c in cs) for cs in by_kmkb.values()), default=0)
        for kmkb, cs in sorted(by_kmkb.items(), key=lambda kv: -sum(c[4] for c in kv[1])):
            s = sum(c[4] for c in cs)
            costs = [c[4] for c in cs]
            key = e(kmkb.upper())
            p(f'<tr class="diag" data-kmkb="{key}"><td><b>{e(kmkb)}</b></td>'
              f'<td class="num">{len(cs)}</td><td class="num">{money(s / len(cs))}</td>'
              f'<td class="num">{money(min(costs))}</td><td class="num">{money(max(costs))}</td>'
              + bar(s / max_diag if max_diag else 0, f"{money(s)} ({pct(s, type_sum).strip()})")
              + '</tr>')
            by_ish = defaultdict(list)
            for c in cs:
                by_ish[c[3]].append(c)
            for ish, cs_i in sorted(by_ish.items(), key=lambda kv: (kv[0] is None, kv[0])):
                s_i = sum(c[4] for c in cs_i)
                costs_i = [c[4] for c in cs_i]
                p(f'<tr class="ishod" data-kmkb="{key}"><td>исход {e(ishod_name(ish))}</td>'
                  f'<td class="num">{len(cs_i)}</td><td class="num">{money(s_i / len(cs_i))}</td>'
                  f'<td class="num">{money(min(costs_i))}</td><td class="num">{money(max(costs_i))}</td>'
                  f'<td class="num">{money(s_i)}</td></tr>')
        p(f'<tr class="total"><td>ИТОГО</td><td class="num">{len(cs_type)}</td>'
          f'<td class="num">{money(type_sum / len(cs_type))}</td><td></td><td></td>'
          f'<td class="num">{money(type_sum)}</td></tr>')
        p('</tbody></table>')

    p(f'<h2>Всего: случаев {n}, сумма {money(total_sum)}</h2>')
    return page(f"Статистика {e(dbf_path.name)}", "\n".join(parts),
                extra_css=_STAT_CSS, script=_STAT_SCRIPT)
