#!/usr/bin/env python3
"""Статистика по DBF-файлу стационара (например uu/0091_016.dbf).

Что делает:
 1. Читает DBF (нужны поля KOTD, KMKB, STOIM, ISHOD; при наличии FACT считает койко-дни).
 2. Делит случаи на дневной стационар (KOTD из --day-kotd, по умолчанию 10 и 15) и
    круглосуточный (остальные отделения).
 3. Сохраняет три файла рядом с DBF:
      statistika_<имя>_<дата_время>.txt   — текстовый отчёт;
      statistika_<имя>_<дата_время>.csv   — таблица для Excel (';', utf-8-sig,
                                            десятичная запятая — русская локаль);
      statistika_<имя>_<дата_время>.html  — наглядный отчёт для браузера.
 4. Прогресс пишется в общий журнал (консоль/GUI) и в statistika_<...>.log.

Примеры запуска:
    omsreg-stat uu/0091_016.dbf
    omsreg-stat uu
    omsreg-stat uu/0091_016.dbf --day-kotd 10,15
"""

from __future__ import annotations

import argparse
import html
import logging
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from omsreg.core import DbfTable, JobError, as_float, as_int, setup_job_logging

ISHOD_NAMES = {
    1: "выписан",
    5: "перевод",
    7: "выписан с улучшением",
    9: "смерть",
    11: "самовольное прерывание лечения",
    12: "выписан без перемен",
}

DAY_TYPE = "Дневной стационар"
ROUND_TYPE = "Круглосуточный стационар"

# Названия отделений по коду KOTD — значение по умолчанию. Коды специфичны для
# учреждения, поэтому названия настраиваются (поле в интерфейсе / --kotd-names /
# аргумент kotd_names у run_stat); этот словарь используется, если ничего не задано.
KOTD_NAMES = {
    23: "Пульмонологическое",
    27: "Терапевтическое",
    61: "Неврологическое",
}


def parse_kotd_names(s) -> dict:
    """Разбирает строку настроек «код=название» (пары через ';' или перевод строки)
    в словарь {int(код): название}. Пустое/некорректное игнорируется."""
    names: dict[int, str] = {}
    for part in str(s or "").replace("\n", ";").split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        k, v = k.strip(), v.strip()
        if k.isdigit() and v:
            names[int(k)] = v
    return names


def format_kotd_names(names: dict) -> str:
    """Обратное к parse_kotd_names: словарь -> «код=название; код=название»."""
    return "; ".join(f"{k}={v}" for k, v in sorted(names.items()))

# имена полей DBF по умолчанию (можно переопределить в интерфейсе/через run_stat)
DEFAULT_FIELDS = {"kotd": "KOTD", "kmkb": "KMKB", "stoim": "STOIM",
                  "ishod": "ISHOD", "fact": "FACT"}

log = logging.getLogger("omsreg.utils.stat_stacionar")


# ----------------------------- форматирование -----------------------------

def money(x) -> str:
    return f"{x:,.2f}".replace(",", " ")


def csv_num(x) -> str:
    """Число для CSV с десятичной запятой (русский Excel; разделитель полей — ';')."""
    return f"{x:.2f}".replace(".", ",")


def pct(part, total) -> str:
    return f"{part / total * 100:5.1f}%" if total else "  0.0%"


def ishod_name(code) -> str:
    if code is None:
        return "(не указан)"
    return f"{code} — {ISHOD_NAMES.get(code, 'неизвестный исход')}"


def kotd_name(code, names=None) -> str:
    """Код отделения с названием, если оно известно: '27 — Терапевтическое'.
    names — словарь {код: название}; по умолчанию берётся встроенный KOTD_NAMES."""
    if code is None:
        return "?"
    name = (KOTD_NAMES if names is None else names).get(code)
    return f"{code} — {name}" if name else str(code)


# ----------------------------- сбор статистики -----------------------------

def collect(table: DbfTable, day_kotd, fields=None):
    """Возвращает (список случаев, число исключённых удалённых, есть_ли_FACT).
    Случай: (тип_стационара, kotd, kmkb, ishod, stoim, fact)."""
    f = dict(DEFAULT_FIELDS)
    if fields:
        f.update({k: str(v).strip().upper() for k, v in fields.items() if str(v).strip()})
    for key in ("kotd", "kmkb", "stoim", "ishod"):
        if not table.has_field(f[key]):
            have = ", ".join(fld.name for fld in table.fields)
            raise ValueError(f"в файле нет поля {f[key]} (есть: {have})")
    has_fact = table.has_field(f["fact"])

    cases, deleted = [], 0
    for rec in table.records:
        if table.is_deleted(rec):
            deleted += 1
            continue
        kotd = as_int(table.value(rec, f["kotd"]))
        kmkb = table.value(rec, f["kmkb"]) or "(без кода МКБ)"
        stoim = as_float(table.value(rec, f["stoim"])) or 0.0
        ishod = as_int(table.value(rec, f["ishod"]))
        fact = as_float(table.value(rec, f["fact"])) if has_fact else None
        st_type = DAY_TYPE if kotd in day_kotd else ROUND_TYPE
        cases.append((st_type, kotd, kmkb, ishod, stoim, fact))
    return cases, deleted, has_fact


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


# ----------------------------- HTML-отчёт -----------------------------

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
    p(f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Статистика {e(dbf_path.name)}</title>
<style>
:root {{
  color-scheme: light;
  --surface: #fcfcfb; --card: #f4f4f2; --ink: #0b0b0b; --ink2: #52514e;
  --border: #e3e2de; --accent: #2a78d6; --bar: #2a78d680; --hover: #eef3fa;
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    color-scheme: dark;
    --surface: #1a1a19; --card: #242423; --ink: #ffffff; --ink2: #c3c2b7;
    --border: #3a3a38; --accent: #3987e5; --bar: #3987e580; --hover: #24303f;
  }}
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; padding: 24px; background: var(--surface); color: var(--ink);
       font: 15px/1.5 system-ui, "Segoe UI", Roboto, sans-serif; }}
.wrap {{ max-width: 1080px; margin: 0 auto; }}
h1 {{ font-size: 22px; margin: 0 0 4px; }}
h2 {{ font-size: 18px; margin: 34px 0 10px; }}
.meta {{ color: var(--ink2); font-size: 13.5px; margin-bottom: 18px; }}
.tiles {{ display: flex; flex-wrap: wrap; gap: 12px; margin: 18px 0; }}
.tile {{ background: var(--card); border: 1px solid var(--border); border-radius: 10px;
         padding: 12px 18px; min-width: 150px; }}
.tile .v {{ font-size: 22px; font-weight: 700; white-space: nowrap; }}
.tile .l {{ font-size: 12.5px; color: var(--ink2); margin-top: 2px; }}
table {{ border-collapse: collapse; width: 100%; margin: 8px 0 4px; font-size: 14px;
         font-variant-numeric: tabular-nums; }}
th {{ text-align: left; color: var(--ink2); font-weight: 600; font-size: 12.5px;
      border-bottom: 2px solid var(--border); padding: 6px 10px; white-space: nowrap; }}
td {{ border-bottom: 1px solid var(--border); padding: 5px 10px; vertical-align: middle; }}
td.num, th.num {{ text-align: right; white-space: nowrap; }}
tr.diag:hover td, table.plain tbody tr:hover td {{ background: var(--hover); }}
tr.ishod td {{ color: var(--ink2); font-size: 13px; border-bottom: 1px dashed var(--border); }}
tr.ishod td:first-child {{ padding-left: 34px; }}
tr.total td {{ font-weight: 700; border-top: 2px solid var(--border); border-bottom: none; }}
.barwrap {{ position: relative; min-width: 130px; }}
.barwrap .bar {{ position: absolute; left: 0; top: 50%; transform: translateY(-50%);
                 height: 10px; background: var(--bar); border-radius: 0 4px 4px 0; }}
.barwrap span {{ position: relative; padding-left: 4px; }}
.controls {{ display: flex; gap: 12px; align-items: center; margin: 14px 0 4px; flex-wrap: wrap; }}
.controls input {{ padding: 6px 10px; border: 1px solid var(--border); border-radius: 8px;
                   background: var(--card); color: var(--ink); font-size: 14px; width: 240px; }}
.controls button {{ padding: 6px 12px; border: 1px solid var(--border); border-radius: 8px;
                    background: var(--card); color: var(--ink); font-size: 14px; cursor: pointer; }}
.controls button:hover, .controls input:focus {{ border-color: var(--accent); outline: none; }}
body.no-ishod tr.ishod {{ display: none; }}
@media print {{ .controls {{ display: none; }} body {{ padding: 0; }} }}
</style></head><body><div class="wrap">""")

    day_list = ", ".join(str(k) for k in sorted(day_kotd))
    p(f"<h1>Статистика по файлу {e(str(dbf_path.name))}</h1>")
    p(f'<div class="meta">Сформировано {datetime.now().strftime("%d.%m.%Y %H:%M")} · '
      f'записей в файле: {total_in_file}'
      + (f' (помечено удалёнными и исключено: {deleted})' if deleted else '')
      + f' · дневной стационар — KOTD: {e(day_list)}, круглосуточный — все остальные</div>')

    # ---------- карточки ----------
    p('<div class="tiles">')
    p(f'<div class="tile"><div class="v">{n}</div><div class="l">случаев</div></div>')
    p(f'<div class="tile"><div class="v">{money(total_sum)}</div><div class="l">сумма STOIM</div></div>')
    if n:
        p(f'<div class="tile"><div class="v">{money(total_sum / n)}</div>'
          f'<div class="l">средняя стоимость случая</div></div>')
    if has_fact and total_fact:
        p(f'<div class="tile"><div class="v">{total_fact:.0f}</div>'
          f'<div class="l">койко-дней (FACT), в среднем {total_fact / n:.1f} на случай</div></div>')
        p(f'<div class="tile"><div class="v">{money(total_sum / total_fact)}</div>'
          f'<div class="l">средняя стоимость койко-дня</div></div>')
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
    p("""<script>
document.getElementById('tgl').onclick = function () {
  document.body.classList.toggle('no-ishod');
};
document.getElementById('flt').oninput = function () {
  var q = this.value.trim().toUpperCase();
  document.querySelectorAll('tr[data-kmkb]').forEach(function (tr) {
    tr.style.display = (!q || tr.getAttribute('data-kmkb').indexOf(q) !== -1) ? '' : 'none';
  });
};
</script></div></body></html>""")
    return "\n".join(parts)


# ----------------------------- запуск -----------------------------

def resolve_dbf_path(target) -> Path:
    """Определяет DBF-файл: сам файл или единственный DBF в папке. Иначе — JobError."""
    path = Path(target)
    if path.is_dir():
        dbfs = sorted(p for p in path.iterdir() if p.suffix.lower() == ".dbf")
        if not dbfs:
            raise JobError(f"В папке {path} нет ни одного DBF-файла")
        if len(dbfs) > 1:
            raise JobError("В папке несколько DBF-файлов — укажите нужный файл явно: "
                           + ", ".join(p.name for p in dbfs))
        return dbfs[0]
    if not path.is_file():
        raise JobError(f"Файл не найден: {path}")
    return path


def parse_day_kotd(day_kotd_str) -> set:
    try:
        return {int(x) for x in str(day_kotd_str).replace(";", ",").split(",") if x.strip()}
    except ValueError as e:
        raise JobError(f"Некорректный список кодов дневного стационара: {day_kotd_str}") from e


def run_stat(target, day_kotd="10,15", fields=None, kotd_names=None,
             extra_handlers=None, console=True) -> dict:
    """Строит статистику стационара и сохраняет .txt/.csv/.html рядом с DBF.
    kotd_names — словарь названий отделений {код: название}; None -> встроенный KOTD_NAMES.
    Прогресс идёт в общий журнал (как у удалялок). Возвращает
    {text, txt_path, csv_path, html_path, log_path, cases}. Фатальные ошибки -> JobError."""
    path = resolve_dbf_path(target)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = path.parent / f"statistika_{path.stem}_{ts}"
    log_path = base.with_suffix(".log")
    setup_job_logging(log, log_path, extra_handlers, console)

    log.info("Файл: %s", path)
    day_kotd_set = parse_day_kotd(day_kotd)
    try:
        table = DbfTable(path)
        log.info("Записей в файле: %d, длина записи %d байт", table.nrec, table.record_len)
        cases, deleted, has_fact = collect(table, day_kotd_set, fields)
    except ValueError as e:
        log.error("%s", e)
        raise JobError(str(e)) from e
    log.info("Отобрано случаев: %d%s", len(cases),
             f", помечено удалёнными и исключено: {deleted}" if deleted else "")

    log.info("Строю текстовый отчёт…")
    report, csv_rows = build_report(path, cases, deleted, has_fact, day_kotd_set, table.nrec,
                                    kotd_names)
    text = "\n".join(report)

    txt_path = base.with_suffix(".txt")
    csv_path = base.with_suffix(".csv")
    html_path = base.with_suffix(".html")
    txt_path.write_text(text + "\n", encoding="utf-8")
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        for row in csv_rows:
            f.write(";".join(str(x) for x in row) + "\n")
    log.info("Строю HTML-отчёт…")
    html_path.write_text(
        build_html(path, cases, deleted, has_fact, day_kotd_set, table.nrec, kotd_names),
        encoding="utf-8")
    log.info("Готово. Файлы: %s, %s, %s", txt_path.name, csv_path.name, html_path.name)

    return {"text": text, "txt_path": txt_path, "csv_path": csv_path,
            "html_path": html_path, "log_path": log_path, "cases": len(cases)}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Статистика по DBF стационара: дневной/круглосуточный -> МКБ -> исходы.")
    parser.add_argument("dbf", help="DBF-файл (например uu/0091_016.dbf) или папка с одним DBF")
    parser.add_argument("--day-kotd", default="10,15",
                        help="коды отделений дневного стационара через запятую (по умолчанию 10,15)")
    parser.add_argument("--kotd-names", default=None,
                        help="названия отделений: «23=Пульмонологическое; 27=Терапевтическое» "
                             "(по умолчанию встроенные)")
    args = parser.parse_args()
    kotd_names = parse_kotd_names(args.kotd_names) if args.kotd_names else None
    try:
        res = run_stat(args.dbf, args.day_kotd, kotd_names=kotd_names)
    except JobError as e:
        print(f"ОШИБКА: {e}", file=sys.stderr)
        sys.exit(2)
    print(res["text"])
    print()
    print(f"Отчёт сохранён:      {res['txt_path']}")
    print(f"Таблица для Excel:   {res['csv_path']}")
    print(f"HTML для просмотра:  {res['html_path']}")


if __name__ == "__main__":
    main()
