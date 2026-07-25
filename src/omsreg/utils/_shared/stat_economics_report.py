"""Текстовый и HTML-отчёт экономики стационара.

Обе формы строятся из одного набора случаев и агрегатов (модуль
omsreg.utils.stat_economics) и показывают одни и те же цифры: доходность койки,
разбор недополученной выручки по причинам, расшифровка КСГ. Агрегаты считает _prepare
(по одному разу на форму отчёта), разделы собирают функции _txt_* и _html_* уже по
готовым числам. HTML самодостаточен (тема из core.report_html + правила ниже).
"""

from __future__ import annotations

import html
from typing import NamedTuple

from omsreg.core import money
from omsreg.core.format import report_stamp
from omsreg.core.report_html import bar, page, tile
from omsreg.utils._shared.stat_common import DAY_TYPE, ROUND_TYPE, group_by, kotd_name
from omsreg.utils.stat_economics import (
    PREFIX_LABEL,
    SHORT_CASE_DAYS,
    TYPE_SHORT,
    base_rates_by_type,
    cause_breakdown,
    dept_rows,
    dx_examples,
    ishod_rows,
    ishod_word,
    koef_counts,
    koef_str,
    kpr_reason,
    kpr_values,
    ksg_rows,
    type_order,
    type_rows,
)

# ----------------------------- ограничения и пороги вывода -----------------------------
# Общие для .txt и .html: одна константа — одно решение, формы не расходятся.

SHORT_KSG_LIMIT = 15   # строк в таблице коротких случаев по группам КСГ
TOP_KSG_LIMIT = 12     # строк в таблице «КСГ: что это за группы» по каждому типу стационара
# Примеров диагнозов в строке КСГ: в .txt колонка идёт последней в строке фиксированной
# ширины (104 символа), в .html ширина не ограничена — поэтому значения разные намеренно.
TXT_DX_LIMIT = 2
HTML_DX_LIMIT = 3
# Доля полностью оплаченных случаев отделения, %: оценка «хорошо / терпимо / плохо».
# Используется только в .html (в .txt цвета нет, столбец «% полн.» без оценки).
FULL_PCT_GOOD = 85
FULL_PCT_WARN = 70

_RULE = "=" * 104  # линейка-разделитель в .txt

# Специфичные для экономики правила поверх общей темы (core.report_html.BASE_CSS):
# цвета good/bad/warn, ширина колонки, свои отступы и классы .note/.main-list/.dx.
_ECON_CSS = (
    ":root { --good:#2e7d32; --bad:#c23b3b; --warn:#b26a00; }\n"
    "@media (prefers-color-scheme: dark) { :root { --ink:#fff; --good:#7fd08a; --bad:#ef8a8a; --warn:#e0a24a; } }\n"
    "body { font:15px/1.5 system-ui,\"Segoe UI\",Roboto,sans-serif; }\n"
    ".wrap { max-width:1120px; }\n"
    "h2 { font-size:18px; margin:28px 0 6px; }\n"
    ".meta { margin-bottom:14px; }\n"
    ".note { color:var(--ink2); font-size:13px; margin:4px 0 8px; }\n"
    ".main-list { margin:8px 0 4px; padding-left:20px; } .main-list li { margin:4px 0; }\n"
    ".tiles { margin:14px 0; }\n"
    "table { margin:6px 0 4px; }\n"
    "tbody tr:hover td { background:var(--hover); }\n"
    ".bad { color:var(--bad); } .good { color:var(--good); } .warn { color:var(--warn); }\n"
    ".dx { color:var(--ink2); font-size:13px; }\n"
    ".barwrap { min-width:110px; }\n"
    # длинные наименования/профиль КСГ: обрезаем многоточием, полный текст — в подсказке (title)
    ".ksg-name, .ksg-prof { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; cursor:help; }\n"
    ".ksg-name { max-width:320px; } .ksg-prof { max-width:150px; }\n"
)

# Инлайновые стили заголовков внутри разделов: в общей теме таких правил нет,
# а разные отступы (10px перед первой таблицей раздела, 12px перед вложенной) значимы.
_H3 = 'style="font-size:15px;margin:10px 0 4px"'
_H3_GAP = 'style="font-size:15px;margin:12px 0 4px"'
_H4 = 'style="font-size:13.5px;margin:8px 0 2px;color:var(--ink2)"'
_P_FORMULA = 'style="font-size:15px;margin:6px 0"'

# ----------------------------- колонки текстовых таблиц -----------------------------
# Одна ширина на шапку и на строки данных: отрицательное значение — выравнивание влево.

_TYPE_W = (-26, 8, 14, 11, 7, 10, 13)        # тип стационара
_DEPT_W = (-26, 8, 8, 14, 11, 7, 9, 13)      # отделения
_CAUSE_W = (-48, 8, 14)                      # причины недополученной оплаты
_SHORT_W = (-12, 8, 8, 8, 14, 9)             # короткие случаи по группам КСГ
_ISHOD_W = (-36, 8, 9, 11, 14)               # исходы
_KSG_NAME_W = 34                             # наименование КСГ
_KSG_PROF_W = 18                             # профиль КСГ
_KSG_W = (-11, -_KSG_NAME_W, -_KSG_PROF_W, 8, 9, 11, 14, 14)


def _row(cells, widths) -> str:
    """Строка текстовой таблицы: ширины из widths (минус — выравнивание влево)."""
    return "".join(f"{c:<{-w}}" if w < 0 else f"{c:>{w}}" for c, w in zip(cells, widths))


def _trunc(s: str, n: int) -> str:
    """Обрезает строку до n символов с многоточием (для колонок фикс. ширины в txt)."""
    s = s or ""
    return s if len(s) <= n else s[: n - 1] + "…"


def _m(x) -> str:
    """Денежная сумма или «—», если значение неизвестно."""
    return money(x) if x is not None else "—"


def _d1(x) -> str:
    """Число с одним знаком после запятой или «—», если значение неизвестно."""
    return f"{x:.1f}" if x is not None else "—"


def _min_full_day(k) -> str:
    """Минимальная длительность с полной оплатой строкой («—», если полных случаев нет)."""
    return f"{k['min_full_day']:.0f}" if k["min_full_day"] is not None else "—"


# ----------------------------- подготовка данных -----------------------------

class _View(NamedTuple):
    """Всё, что нужно обеим формам отчёта: итоги, признаки наличия полей и агрегаты.

    Собирается в _prepare один раз на форму отчёта: раньше эти 17 строк подготовки
    повторялись в .txt и .html, а агрегаты (отделения, КСГ, причины) пересчитывались
    внутри разделов. Теперь оба вида отчёта форматируют одни и те же готовые числа и
    по построению не могут разойтись.
    """

    cases: list
    n: int
    total: float
    total_under: float
    full_n: int
    has_kpr: bool           # есть KOEF_PR — можно считать недополученное
    has_fact: bool          # есть FACT — известны койко-дни
    has_g: bool             # есть GRUPPA — можно разбирать КСГ
    has_ishod: bool         # есть ISHOD
    has_full_model: bool    # есть все множители формулы оплаты
    depts: list             # агрегаты по отделениям
    ksg: list               # агрегаты по группам КСГ
    groups_with_full: set   # группы КСГ, в которых есть полностью оплаченные случаи
    causes: list            # разбивка недополученного по причинам (список Cause)
    reserve: float          # сколько реально вернуть длительностью лечения


def _prepare(cases, avail) -> _View:
    """Считает итоги и агрегаты по случаям — общая часть .txt и .html."""
    has_g = avail["gruppa"] is not None
    has_kpr = avail["koef_pr"] is not None
    ksg = ksg_rows(cases) if has_g else []
    groups_with_full = {k["g"] for k in ksg if k["has_full"]}
    causes = cause_breakdown(cases, groups_with_full) if has_kpr else []
    return _View(
        cases=cases,
        n=len(cases),
        total=sum(c["stoim"] for c in cases),
        total_under=sum(c["underpaid"] for c in cases),
        full_n=sum(1 for c in cases if not c["interrupted"]),
        has_kpr=has_kpr,
        has_fact=avail["fact"] is not None,
        has_g=has_g,
        has_ishod=avail["ishod"] is not None,
        has_full_model=has_g and all(avail[k] for k in ("koef_z", "koef_up", "koef_pr")),
        depts=dept_rows(cases),
        ksg=ksg,
        groups_with_full=groups_with_full,
        causes=causes,
        # признак «возвратно» берём из Cause.recoverable, а не по порядку строк
        reserve=sum(c["underpaid"] for cause in causes if cause.recoverable for c in cause.cases),
    )


def _short_ksg(v: _View) -> list:
    """Группы КСГ с короткими случаями: круглосуточные первыми, внутри — по потерям."""
    return sorted([k for k in v.ksg if k["short_n"]],
                  key=lambda k: (type_order(k["type"]), -k["short_lost"]))


def _by_kotd(cases) -> dict:
    """Случаи, разложенные по отделениям (KOTD)."""
    return group_by(cases, lambda c: c["kotd"])


def _day_depts(cases) -> list:
    """Коды отделений дневного стационара, встретившиеся в файле (строками, по порядку)."""
    return sorted(str(k) for k in {c["kotd"] for c in cases
                                   if c["type"] == DAY_TYPE and c["kotd"] is not None})


def _kz_range(v: _View) -> tuple | None:
    """Диапазон веса КСГ по всем группам файла: (минимум, максимум) или None."""
    kz_all = [k["kz_range"] for k in v.ksg if k["kz_range"]]
    if not kz_all:
        return None
    return min(r[0] for r in kz_all), max(r[1] for r in kz_all)


def _koef_counts_str(cases, key, reverse=False) -> str:
    """Значения коэффициента с числом случаев одной строкой: «×1 — 380, ×0.5 — 24»."""
    return ", ".join(f"×{val:g} — {n}" for val, n in koef_counts(cases, key, reverse=reverse))


def _base_rate_parts(cases, esc=str) -> list:
    """Базовая ставка по типам КСГ подписями «st… (…) ~1 234.00 ₽», по убыванию.

    esc — как готовить подпись к выводу: str (как есть, для .txt) или html.escape.
    """
    bs = base_rates_by_type(cases)
    return [f"{esc(PREFIX_LABEL.get(p, p))} ~{money(val)} ₽"
            for p, val in sorted(bs.items(), key=lambda kv: -kv[1])]


# ----------------------------- текстовый отчёт: разделы -----------------------------

def _txt_header(deleted, v: _View) -> list:
    """Шапка: название отчёта, время формирования, итоги одной строкой."""
    head = f"Случаев: {v.n}"
    if deleted:
        head += f" (исключено удалённых: {deleted})"
    head += f"; полностью оплачено {v.full_n}; оплачено {money(v.total)} ₽"
    if v.has_kpr:
        head += f"; недополучено {money(v.total_under)} ₽"
    return [
        _RULE,
        "ЭКОНОМИКА И ЭФФЕКТИВНОСТЬ СТАЦИОНАРА (ОМС, оплата по КСГ)",
        f"Сформировано: {report_stamp()}",
        head,
        _RULE,
    ]


def _txt_main(v: _View, names) -> list:
    """«ГЛАВНОЕ»: лучшая койка, худшее по недополученному, размер возвратного резерва."""
    out = ["", "ГЛАВНОЕ"]
    ranked = [r for r in v.depts if r["per_day"]]
    if ranked:
        out.append(f"  • Лучшая по доходности койка: {kotd_name(ranked[0]['kotd'], names)} — "
                   f"{money(ranked[0]['per_day'])} ₽/койко-день.")
    if v.depts:
        worst = max(v.depts, key=lambda r: r["under"])
        if worst["under"]:
            out.append(f"  • Больше всех недополучено: {kotd_name(worst['kotd'], names)} — "
                       f"{money(worst['under'])} ₽ (полных {worst['full_pct']:.0f}%).")
    if v.has_kpr and v.total:
        out.append(f"  • Недополучено из-за сниженной оплаты {money(v.total_under)} ₽ "
                   f"({v.n - v.full_n} случаев из {v.n}).")
        if v.has_fact and v.has_g:
            out.append(f"    Реально вернуть длительностью лечения можно ≈{money(v.reserve)} ₽ "
                       f"(+{v.reserve / v.total * 100:.1f}% к оплате) — это короткие случаи "
                       f"1–{SHORT_CASE_DAYS} дня в группах,")
            out.append("    где есть полностью оплаченные случаи. Остальное длительностью не возвращается")
            out.append("    (переводы, прерванные по правилам КСГ, группы без полных случаев) — см. ниже.")
    return out


def _txt_types(v: _View) -> list:
    """«ПО ТИПАМ СТАЦИОНАРА»: круглосуточный и дневной одной таблицей."""
    out = ["", "ПО ТИПАМ СТАЦИОНАРА",
           "  " + _row(("тип", "случаев", "оплачено", "₽/к-день",
                        "ср.дни", "% полных", "недополуч."), _TYPE_W)]
    for t in type_rows(v.cases):
        under = money(t["under"]) if t["under"] else "—"
        out.append("  " + _row((t["type"], t["n"], money(t["sum"]), _m(t["per_day"]),
                                _d1(t["days"]), f"{t['full_pct']:.0f}%", under), _TYPE_W))
    return out


def _txt_depts(v: _View, names) -> list:
    """«КАК ОТРАБОТАЛИ ОТДЕЛЕНИЯ»: по типам стационара, внутри — по доходности койки."""
    out = ["", "КАК ОТРАБОТАЛИ ОТДЕЛЕНИЯ (по типам стационара, внутри — по доходности койки)",
           "  «₽/к-день» — вся оплата отделения ÷ его койко-дни; "
           "«% полн.» — доля полностью оплаченных случаев.",
           "  " + _row(("отделение", "тип", "случаев", "оплачено", "₽/к-день",
                        "ср.дни", "% полн.", "недополуч."), _DEPT_W)]
    for r in v.depts:
        under = money(r["under"]) if r["under"] else "—"
        out.append("  " + _row((kotd_name(r["kotd"], names), TYPE_SHORT[r["type"]], r["n"],
                                money(r["sum"]), _m(r["per_day"]), _d1(r["days"]),
                                f"{r['full_pct']:.0f}%", under), _DEPT_W))
    return out


def _txt_cause_row(cause) -> str:
    """Строка таблицы причин: подпись, случаи, недополучено и словесный признак возвратности."""
    under = money(sum(c["underpaid"] for c in cause.cases))
    return "    " + _row((cause.label, len(cause.cases), under), _CAUSE_W) + f"   {cause.note}"


def _txt_causes(v: _View) -> list:
    """Причины недополученной оплаты по типам стационара (что вернуть длительностью)."""
    out = []
    for st in (ROUND_TYPE, DAY_TYPE):
        st_cases = [c for c in v.cases if c["type"] == st]
        st_under = sum(c["underpaid"] for c in st_cases)
        if not st_under:
            continue
        st_int = sum(1 for c in st_cases if c["interrupted"])
        out.append("")
        out.append(f"  {st} — недополучено {money(st_under)} ₽:")
        out.append("    " + _row(("причина", "случаев", "недополуч."), _CAUSE_W)
                   + "   вернуть длительностью?")
        out += [_txt_cause_row(c) for c in cause_breakdown(st_cases, v.groups_with_full) if c.cases]
        out.append("    " + _row(("ИТОГО", st_int, money(st_under)), _CAUSE_W))
    return out


def _txt_short_ksg(v: _View) -> list:
    """Короткие случаи по группам КСГ: где недоплату снимает длительность лечения."""
    short_g = _short_ksg(v)
    if not short_g:
        return []
    out = ["",
           f"  Короткие случаи (1–{SHORT_CASE_DAYS} дня) по группам КСГ:",
           "    «полные?»: «да» — случай короче нормы группы, можно довести до полной оплаты;",
           "    «нет» — в файле нет полных случаев этой группы, поэтому по данным нельзя сказать,",
           "    даёт ли большая длительность полную оплату; нужно свериться с правилами КСГ.",
           "    " + _row(("КСГ", "тип", "случаев", "дни", "недополуч.", "полные?"), _SHORT_W)]
    for k in short_g[:SHORT_KSG_LIMIT]:
        days = ", ".join(str(d) for d in k["short_days"])
        out.append("    " + _row((k["g"], TYPE_SHORT[k["type"]], k["short_n"], days,
                                  money(k["short_lost"]), "да" if k["has_full"] else "нет"), _SHORT_W))
    out.append("    " + _row(("ИТОГО", "", sum(k["short_n"] for k in short_g), "",
                              money(sum(k["short_lost"] for k in short_g))), _SHORT_W))
    if len(short_g) > SHORT_KSG_LIMIT:   # ИТОГО считается по всем группам, а видно не все
        out.append(f"    Показаны первые {SHORT_KSG_LIMIT} групп из {len(short_g)}; "
                   "ИТОГО — по всем группам.")
    return out


def _txt_underpaid(v: _View) -> list:
    """«ГДЕ НЕДОПОЛУЧЕНО»: разбор по причинам и короткие случаи по группам КСГ."""
    if not v.has_kpr:
        return []
    out = ["", "ГДЕ НЕДОПОЛУЧЕНО И ЧТО ИЗ ЭТОГО МОЖНО ВЕРНУТЬ",
           "  Сниженная оплата бывает по разным причинам; "
           "длительностью лечения устраняется только часть",
           "  (строка «в группе есть полные случаи»). Разбивка по типам стационара:"]
    out += _txt_causes(v)
    if v.has_fact and v.has_g:
        out += _txt_short_ksg(v)
    return out


def _txt_ishod_table(subset, indent) -> list:
    """Таблица «исход → оплата» для подмножества случаев с заданным отступом."""
    out = [indent + _row(("исход", "случаев", "полных", "сниженных", "недополуч."), _ISHOD_W)]
    out += [indent + _row((ishod_word(r["ishod"]), r["n"], r["full"], r["reduced"],
                           money(r["under"])), _ISHOD_W)
            for r in ishod_rows(subset)]
    return out


def _txt_ishod(v: _View, names) -> list:
    """«КАК ИСХОД ВЛИЯЕТ НА ОПЛАТУ»: по типам стационара и по отделениям внутри типа."""
    if not (v.has_kpr and v.has_ishod):
        return []
    out = ["", "КАК ИСХОД ВЛИЯЕТ НА ОПЛАТУ (по типам стационара и отделениям)",
           "  У каких исходов чаще снижается оплата (по данным файла; точные правила — в КСГ)."]
    for st in (ROUND_TYPE, DAY_TYPE):
        st_cases = [c for c in v.cases if c["type"] == st]
        if not st_cases:
            continue
        by_k = _by_kotd(st_cases)
        out.append("")
        out.append(f"  {st}:")
        out += _txt_ishod_table(st_cases, "    ")
        if len(by_k) > 1:   # если отделение одно, таблица типа = таблице отделения — не дублируем
            for kotd in sorted(by_k, key=lambda k: (k is None, str(k))):
                out.append("")
                out.append(f"    отделение {kotd_name(kotd, names)}:")
                out += _txt_ishod_table(by_k[kotd], "      ")
    return out


def _txt_formula(v: _View) -> list:
    """«КАК ФОРМИРУЕТСЯ ОПЛАТА СЛУЧАЯ»: разбор четырёх множителей по данным файла."""
    if not v.has_full_model:
        return []
    bs_parts = _base_rate_parts(v.cases)
    kz_rng = _kz_range(v)
    out = ["", "КАК ФОРМИРУЕТСЯ ОПЛАТА СЛУЧАЯ",
           "  Оплата идёт за случай целиком (не за день) и равна произведению четырёх множителей:",
           "",
           "    оплата = базовая ставка × вес группы КСГ × поправочный коэффициент × коэффициент оплаты",
           ""]
    if bs_parts:
        out.append("  • Базовая ставка — единая стоимость случая для типа КСГ, восстановлена из данных:")
        out.append("    " + "; ".join(bs_parts) + ".")
        out.append("    (Тип КСГ по коду st…/ds… — не то же, "
                   "что тип стационара по отделению в других разделах.)")
    if kz_rng:
        out.append("  • Вес группы КСГ (столбец «вес КСГ») — во сколько раз группа дороже базовой ставки;")
        out.append(f"    постоянен внутри группы, в файле {kz_rng[0]:g}–{kz_rng[1]:g}.")
    out.append("  • Поправочный коэффициент (столбец «попр.коэф.») — задаётся по случаю. Значения в файле")
    out.append("    (случаев): " + _koef_counts_str(v.cases, "kup") + ".")
    out.append("    Это не «уровень отделения» (в одном отделении бывают разные значения); точный смысл —")
    out.append("    в правилах КСГ и тарифном соглашении, из выгрузки не определяется. В этом файле он")
    out.append("    постоянен внутри каждой группы КСГ, поэтому показан в таблице отдельным столбцом.")
    out.append("  • Коэффициент оплаты — полнота случая: чем меньше, тем сильнее снижена оплата.")
    out.append("    Значения в файле (случаев): "
               + _koef_counts_str(v.cases, "kpr", reverse=True) + ".")
    out.append("    Разбор недополученных сумм — в разделе «Где недополучено».")
    return out


def _txt_ksg(v: _View) -> list:
    """«КСГ: ЧТО ЭТО ЗА ГРУППЫ»: топ групп по обороту с наименованием, весами и диагнозами."""
    if not v.has_g:
        return []
    out = ["", "КСГ: ЧТО ЭТО ЗА ГРУППЫ И СКОЛЬКО ПРИНОСЯТ (по типам стационара, топ по обороту)",
           "  Группа подписана официальным наименованием и профилем из справочника КСГ, плюс реальные",
           "  диагнозы (коды МКБ) из этого файла.",
           "  «вес КСГ» и «попр.коэф.» — множители группы (см. блок выше); «тариф полн.» = оплата за",
           "  полностью пролеченный случай (базовая ставка × вес × попр.коэф.); «мин.полн.день» =",
           "  минимальная длительность, при которой была полная оплата («—» — полных случаев нет)."]
    for st in (ROUND_TYPE, DAY_TYPE):
        st_ksg = sorted([k for k in v.ksg if k["type"] == st], key=lambda k: -k["sum"])[:TOP_KSG_LIMIT]
        if not st_ksg:
            continue
        out.append("")
        out.append(f"  {st}:")
        out.append("    " + _row(("КСГ", "наименование КСГ", "профиль", "случаев", "вес КСГ",
                                  "попр.коэф.", "тариф полн.", "мин.полн.день"), _KSG_W)
                   + "  диагнозы (МКБ)")
        for k in st_ksg:
            # обрезаем на 1 символ меньше ширины колонки, чтобы остался пробел до следующей
            name = _trunc(k["title"] or k["chapter"], _KSG_NAME_W - 1)
            out.append("    " + _row((k["g"], name, _trunc(k["profile"], _KSG_PROF_W - 1), k["n"],
                                      koef_str(k["kz"], k["kz_range"]), koef_str(k["kup"], k["kup_range"]),
                                      _m(k["full_tariff"]), _min_full_day(k)), _KSG_W)
                       + f"  {dx_examples(k['dx'], TXT_DX_LIMIT)}")
    return out


def _txt_method(dbf_path, v: _View) -> list:
    """«МЕТОДИКА»: как считаются числа отчёта и откуда взяты коэффициенты."""
    out = ["", _RULE, "МЕТОДИКА",
           "  • Оплата идёт за случай по группе КСГ, а не за день: сумма зависит от веса группы КСГ,",
           "    поправочного коэффициента и коэффициента оплаты (полноты пролеченного случая)."]
    if v.has_kpr:
        out.append("  • Коэффициент оплаты: 1.0 = случай оплачен полностью; меньше 1 = снижена "
                   "(короткий случай,")
        out.append("    перевод, смерть, самовольный уход, длительность ниже нормы группы). "
                   "Значения в файле:")
        for kpr in kpr_values(v.cases):
            cs = [c for c in v.cases if c["kpr"] == kpr]
            if kpr >= 1:
                out.append(f"      ×{kpr:g} — {len(cs)} случаев (полная оплата)")
            else:
                lost = sum(c["underpaid"] for c in cs)
                out.append(f"      ×{kpr:g} — {len(cs)}, недополучено {money(lost)} ₽ ({kpr_reason(cs)})")
    out.append("  • ₽/койко-день = вся оплата отделения ÷ все его койко-дни (характеризует оборот койки).")
    day_depts = _day_depts(v.cases)
    if day_depts:
        out.append(f"  • Дневной/круглосуточный — по отделениям (KOTD): дневные — {', '.join(day_depts)}; "
                   "остальные — круглосуточные.")
    if v.has_full_model:
        bs = base_rates_by_type(v.cases)
        pr = [f"КСГ {p}… ~{money(bs[p])} ₽" for p in ("st", "ds") if p in bs]
        if pr:
            out.append("  • Базовая ставка (восстановлена из данных): " + "; ".join(pr) + ".")
    out.append(f"  Источник данных: {dbf_path}.")
    out.append(_RULE)
    return out


def build_report(dbf_path, cases, deleted, avail, names=None) -> list:
    """Строит текстовую форму отчёта. -> список строк (без завершающих переводов строки).

    Разделы собирают функции _txt_*; те же числа в HTML-форме даёт build_html.
    """
    v = _prepare(cases, avail)
    return (_txt_header(deleted, v)
            + _txt_main(v, names)
            + _txt_types(v)
            + _txt_depts(v, names)
            + _txt_underpaid(v)
            + _txt_ishod(v, names)
            + _txt_formula(v)
            + _txt_ksg(v)
            + _txt_method(dbf_path, v))


# ----------------------------- HTML-отчёт: разделы -----------------------------

def _html_head(deleted, v: _View) -> list:
    """Заголовок страницы и строка «сформировано / случаев»."""
    meta = f'Сформировано {report_stamp(seconds=False)} · случаев: {v.n}'
    if deleted:
        meta += f' (исключено удалённых: {deleted})'
    meta += f' · полностью оплачено: {v.full_n}'
    return ["<h1>Экономика и эффективность стационара</h1>", f'<div class="meta">{meta}</div>']


def _html_tiles(v: _View, names) -> list:
    """Карточки с ключевыми числами: оплачено, недополучено, резерв, лучшая койка."""
    e = html.escape
    out = ['<div class="tiles">', tile(f"{money(v.total)} ₽", "оплачено всего")]
    if v.has_kpr:
        out.append(tile(f"{money(v.total_under)} ₽", "недополучено из-за сниженной оплаты", "bad"))
        if v.has_fact and v.has_g and v.total:
            out.append(tile(f"{money(v.reserve)} ₽",
                            f"реально вернуть длительностью (+{v.reserve / v.total * 100:.1f}%)", "good"))
    if v.depts and v.depts[0]["per_day"]:
        out.append(tile(e(kotd_name(v.depts[0]["kotd"], names)),
                        f'лучшая койка · {money(v.depts[0]["per_day"])} ₽/день'))
    out.append('</div>')
    return out


def _html_types(v: _View) -> list:
    """Таблица по типам стационара."""
    e = html.escape
    out = ['<h2>По типам стационара</h2>'
           '<table><thead><tr><th>тип</th><th class="num">случаев</th><th class="num">оплачено</th>'
           '<th class="num">₽/койко-день</th><th class="num">ср. дни</th><th class="num">% полных</th>'
           '<th class="num">недополуч.</th></tr></thead><tbody>']
    for t in type_rows(v.cases):
        under = money(t["under"]) if t["under"] else ""
        out.append(f'<tr><td>{e(t["type"])}</td><td class="num">{t["n"]}</td>'
                   f'<td class="num">{money(t["sum"])}</td><td class="num">{_m(t["per_day"])}</td>'
                   f'<td class="num">{_d1(t["days"])}</td><td class="num">{t["full_pct"]:.0f}%</td>'
                   f'<td class="num bad">{under}</td></tr>')
    out.append('</tbody></table>')
    return out


def _full_pct_cls(full_pct) -> str:
    """Класс оценки доли полностью оплаченных случаев отделения (только .html)."""
    if full_pct >= FULL_PCT_GOOD:
        return "good"
    return "warn" if full_pct >= FULL_PCT_WARN else "bad"


def _html_depts(v: _View, names) -> list:
    """Таблица отделений с полосками доходности койки."""
    e = html.escape
    out = ['<h2>Как отработали отделения</h2>'
           '<p class="note">По типам стационара, внутри — по доходности койки (₽ на койко-день = '
           'оплата ÷ койко-дни). «% полных» — доля полностью оплаченных случаев.</p>'
           '<table><thead><tr><th>отделение</th><th>тип</th><th class="num">случаев</th>'
           '<th class="num">оплачено</th><th class="num">₽/койко-день</th><th class="num">ср. дни</th>'
           '<th class="num">% полных</th><th class="num">недополуч.</th></tr></thead><tbody>']
    maxpd = max((r["per_day"] or 0 for r in v.depts), default=0)
    for r in v.depts:
        # полоса доходности койки: доля от лучшего отделения, ширина без дробной части
        pd_bar = bar((r["per_day"] or 0) / maxpd if maxpd else 0, _m(r["per_day"]), digits=0)
        under = money(r["under"]) if r["under"] else ""
        out.append(f'<tr><td>{e(kotd_name(r["kotd"], names))}</td><td>{e(TYPE_SHORT[r["type"]])}</td>'
                   f'<td class="num">{r["n"]}</td><td class="num">{money(r["sum"])}</td>'
                   f'<td class="num">{pd_bar}</td><td class="num">{_d1(r["days"])}</td>'
                   f'<td class="num {_full_pct_cls(r["full_pct"])}">{r["full_pct"]:.0f}%</td>'
                   f'<td class="num bad">{under}</td></tr>')
    out.append('</tbody></table>')
    return out


def _html_causes(v: _View) -> list:
    """Таблицы причин недополученной оплаты по типам стационара."""
    e = html.escape
    out = []
    for st in (ROUND_TYPE, DAY_TYPE):
        st_cases = [c for c in v.cases if c["type"] == st]
        st_under = sum(c["underpaid"] for c in st_cases)
        if not st_under:
            continue
        st_int = sum(1 for c in st_cases if c["interrupted"])
        out.append(f'<h3 {_H3}>{e(st)} — недополучено {money(st_under)} ₽</h3>'
                   '<table><thead><tr><th>причина</th><th class="num">случаев</th>'
                   '<th class="num">недополуч.</th><th>вернуть длительностью?</th></tr></thead><tbody>')
        for cause in cause_breakdown(st_cases, v.groups_with_full):
            if cause.cases:
                cls = "good" if cause.recoverable else "warn"
                out.append(f'<tr><td>{e(cause.label)}</td><td class="num">{len(cause.cases)}</td>'
                           f'<td class="num bad">{money(sum(c["underpaid"] for c in cause.cases))}</td>'
                           f'<td class="{cls}">{e(cause.note)}</td></tr>')
        out.append(f'<tr class="total"><td>ИТОГО</td><td class="num">{st_int}</td>'
                   f'<td class="num bad">{money(st_under)}</td><td></td></tr></tbody></table>')
    return out


def _html_short_ksg(v: _View) -> list:
    """Таблица коротких случаев по группам КСГ."""
    e = html.escape
    short_g = _short_ksg(v)
    if not short_g:
        return []
    out = [f'<h3 {_H3_GAP}>Короткие случаи (1–{SHORT_CASE_DAYS} дня) по группам КСГ</h3>'
           '<p class="note">«Полные в группе»: <b>да</b> — случай короче нормы группы, можно довести '
           'до полной оплаты. <b>Нет</b> — в файле нет полных случаев этой группы, поэтому по данным '
           'нельзя сказать, даёт ли большая длительность полную оплату; нужно свериться с правилами КСГ.</p>'
           '<table><thead><tr><th>КСГ</th><th>тип</th><th class="num">случаев</th><th class="num">дни</th>'
           '<th class="num">недополуч.</th><th class="num">полные?</th></tr></thead><tbody>']
    for k in short_g[:SHORT_KSG_LIMIT]:
        days = ", ".join(str(d) for d in k["short_days"])
        hf = '<span class="good">да</span>' if k["has_full"] else '<span class="warn">нет</span>'
        out.append(f'<tr><td><b>{e(k["g"])}</b></td><td>{e(TYPE_SHORT[k["type"]])}</td>'
                   f'<td class="num">{k["short_n"]}</td>'
                   f'<td class="num">{e(days)}</td><td class="num bad">{money(k["short_lost"])}</td>'
                   f'<td class="num">{hf}</td></tr>')
    out.append(f'<tr class="total"><td>ИТОГО</td><td></td>'
               f'<td class="num">{sum(k["short_n"] for k in short_g)}</td>'
               f'<td></td><td class="num bad">{money(sum(k["short_lost"] for k in short_g))}</td>'
               f'<td></td></tr></tbody></table>')
    if len(short_g) > SHORT_KSG_LIMIT:   # ИТОГО считается по всем группам, а видно не все
        out.append(f'<p class="note">Показаны первые {SHORT_KSG_LIMIT} групп из {len(short_g)}; '
                   'ИТОГО — по всем группам.</p>')
    return out


def _html_underpaid(v: _View) -> list:
    """Раздел «Где недополучено и что из этого можно вернуть»."""
    if not v.has_kpr:
        return []
    out = ['<h2>Где недополучено и что из этого можно вернуть</h2>'
           '<p class="note">Сниженная оплата бывает по разным причинам; длительностью лечения '
           'устраняется только строка «в группе есть полные случаи». Разбивка по типам стационара:</p>']
    out += _html_causes(v)
    if v.has_fact and v.has_g:
        out += _html_short_ksg(v)
    return out


def _html_ishod_table(subset) -> list:
    """Таблица «исход → оплата» для подмножества случаев."""
    e = html.escape
    out = ['<table><thead><tr><th>исход</th><th class="num">случаев</th><th class="num">полных</th>'
           '<th class="num">сниженных</th><th class="num">недополуч.</th></tr></thead><tbody>']
    for r in ishod_rows(subset):
        under = money(r["under"]) if r["under"] else ""
        out.append(f'<tr><td>{e(ishod_word(r["ishod"]))}</td><td class="num">{r["n"]}</td>'
                   f'<td class="num good">{r["full"]}</td><td class="num">{r["reduced"]}</td>'
                   f'<td class="num bad">{under}</td></tr>')
    out.append('</tbody></table>')
    return out


def _html_ishod(v: _View, names) -> list:
    """Раздел «Как исход влияет на оплату»: по типам стационара и отделениям."""
    if not (v.has_kpr and v.has_ishod):
        return []
    e = html.escape
    out = ['<h2>Как исход влияет на оплату</h2>'
           '<p class="note">По типам стационара и отделениям. У каких исходов чаще снижается оплата '
           '(по данным файла; точные правила — в КСГ).</p>']
    for st in (ROUND_TYPE, DAY_TYPE):
        st_cases = [c for c in v.cases if c["type"] == st]
        if not st_cases:
            continue
        by_k = _by_kotd(st_cases)
        out.append(f'<h3 {_H3_GAP}>{e(st)}</h3>')
        out += _html_ishod_table(st_cases)
        if len(by_k) > 1:   # одно отделение — таблица типа совпадает с отделением, не дублируем
            for kotd in sorted(by_k, key=lambda k: (k is None, str(k))):
                out.append(f'<h4 {_H4}>отделение {e(kotd_name(kotd, names))}</h4>')
                out += _html_ishod_table(by_k[kotd])
    return out


def _html_formula(v: _View) -> list:
    """Раздел «Как формируется оплата случая»: таблица четырёх множителей."""
    if not v.has_full_model:
        return []
    e = html.escape
    bs_parts = _base_rate_parts(v.cases, e)
    kz_rng = _kz_range(v)
    kzr = f'{kz_rng[0]:g}–{kz_rng[1]:g}' if kz_rng else '—'
    up_str = _koef_counts_str(v.cases, "kup")
    pr_str = _koef_counts_str(v.cases, "kpr", reverse=True)
    return [
        '<h2>Как формируется оплата случая</h2>',
        f'<p {_P_FORMULA}><b>оплата = базовая ставка × вес группы КСГ × '
        'поправочный коэффициент × коэффициент оплаты</b></p>',
        '<table><thead><tr><th>множитель</th><th>что показывает</th><th>постоянен</th>'
        '<th>значения в файле</th></tr></thead><tbody>',
        f'<tr><td><b>Базовая ставка</b></td><td>единая стоимость случая, восстановлена из данных; '
        f'подписывается кодом КСГ (st…/ds…), а не «круглосуточный/дневной»</td>'
        f'<td>тип КСГ (код st…/ds…)</td><td>{"; ".join(bs_parts) or "—"}</td></tr>',
        f'<tr><td><b>Вес группы КСГ</b> (столбец «вес КСГ»)</td>'
        f'<td>во сколько раз группа дороже базовой ставки</td><td>группа КСГ</td>'
        f'<td>{e(kzr)}</td></tr>',
        f'<tr><td><b>Поправочный коэффициент</b> (столбец «попр. коэф.»)</td>'
        f'<td>корректирует стоимость; это <b>не «уровень отделения»</b> — точный смысл в правилах '
        f'КСГ / тарифном соглашении, из данных не определяется</td>'
        f'<td>по случаю (в этом файле — один на группу КСГ)</td>'
        f'<td>{e(up_str)}</td></tr>',
        f'<tr><td><b>Коэффициент оплаты</b></td>'
        f'<td>полнота случая: 1.0 — оплачен полностью, меньше 1 — снижена (см. «Где недополучено»)</td>'
        f'<td>по случаю</td><td>{e(pr_str)}</td></tr>',
        '</tbody></table>',
        '<p class="note"><b>Тариф полн. случая</b> в таблице ниже = базовая ставка × вес × '
        'поправочный коэффициент — оплата за полностью пролеченный случай.</p>',
    ]


def _html_ksg(v: _View) -> list:
    """Раздел «КСГ: что это за группы и сколько приносят»."""
    if not v.has_g:
        return []
    e = html.escape
    out = ['<h2>КСГ: что это за группы и сколько приносят</h2>'
           '<p class="note">По типам стационара, топ по обороту. Группа подписана официальным '
           'наименованием и профилем из справочника КСГ (полное название — во всплывающей подсказке '
           'при наведении) и реальными диагнозами (МКБ) из этого файла. «вес КСГ» и «попр. коэф.» — '
           'множители группы (см. блок выше); «тариф полн. случая» = оплата за полностью пролеченный '
           'случай; «мин. полный день» = минимальная длительность, при которой была полная оплата. '
           '«—» — полных случаев в группе нет.</p>']
    for st in (ROUND_TYPE, DAY_TYPE):
        st_ksg = sorted([k for k in v.ksg if k["type"] == st], key=lambda k: -k["sum"])[:TOP_KSG_LIMIT]
        if not st_ksg:
            continue
        out.append(f'<h3 {_H3}>{e(st)}</h3>'
                   '<table><thead><tr><th>КСГ</th><th>наименование</th><th>профиль</th>'
                   '<th class="num">случаев</th><th class="num">вес КСГ</th><th class="num">попр. коэф.</th>'
                   '<th class="num">тариф полн. случая, ₽</th><th class="num">мин. полный день</th>'
                   '<th>диагнозы (примеры)</th></tr></thead><tbody>')
        for k in st_ksg:
            name = k["title"] or k["chapter"]
            out.append(f'<tr><td><b>{e(k["g"])}</b></td>'
                       f'<td class="ksg-name" title="{e(name)}">{e(name)}</td>'
                       f'<td class="ksg-prof" title="{e(k["profile"])}">{e(k["profile"])}</td>'
                       f'<td class="num">{k["n"]}</td>'
                       f'<td class="num">{e(koef_str(k["kz"], k["kz_range"]))}</td>'
                       f'<td class="num">{e(koef_str(k["kup"], k["kup_range"]))}</td>'
                       f'<td class="num">{_m(k["full_tariff"])}</td>'
                       f'<td class="num">{_min_full_day(k)}</td>'
                       f'<td class="dx">{e(dx_examples(k["dx"], HTML_DX_LIMIT))}</td></tr>')
        out.append('</tbody></table>')
    return out


def _html_method(dbf_path, v: _View) -> list:
    """Раздел «Методика» списком."""
    e = html.escape
    out = ['<h2>Методика</h2><ul class="note main-list">',
           '<li>Оплата идёт за случай по группе КСГ, а не за день: сумма зависит от веса группы КСГ, '
           'поправочного коэффициента и коэффициента оплаты (полноты пролеченного случая).</li>']
    if v.has_kpr:
        items = []
        for kpr in kpr_values(v.cases):
            cs = [c for c in v.cases if c["kpr"] == kpr]
            if kpr >= 1:
                items.append(f"×{kpr:g} — {len(cs)} случаев (полная оплата)")
            else:
                lost = sum(c["underpaid"] for c in cs)
                items.append(f"×{kpr:g} — {len(cs)}, недополучено {money(lost)} ₽ ({e(kpr_reason(cs))})")
        out.append('<li>Коэффициент оплаты: 1.0 — полностью, меньше 1 — снижена (короткий случай, перевод, '
                   'смерть, самовольный уход, длительность ниже нормы группы). Значения в файле: '
                   + "; ".join(items) + '.</li>')
    out.append('<li>₽/койко-день = вся оплата отделения ÷ все его койко-дни (оборот койки).</li>')
    day_depts = _day_depts(v.cases)
    if day_depts:
        out.append(f'<li>Дневной/круглосуточный — по отделениям (KOTD): дневные — {e(", ".join(day_depts))}; '
                   'остальные — круглосуточные.</li>')
    out.append(f'<li>Источник данных: {e(dbf_path.name)}.</li>')
    out.append('</ul>')
    return out


def build_html(dbf_path, cases, deleted, avail, names=None) -> str:
    """Строит HTML-форму отчёта. -> самодостаточная страница строкой.

    Те же числа, что и в текстовой форме (build_report); разделы собирают
    функции _html_*, оформление — core.report_html + _ECON_CSS.
    """
    e = html.escape
    v = _prepare(cases, avail)
    parts = (_html_head(deleted, v)
             + _html_tiles(v, names)
             + _html_types(v)
             + _html_depts(v, names)
             + _html_underpaid(v)
             + _html_ishod(v, names)
             + _html_formula(v)
             + _html_ksg(v)
             + _html_method(dbf_path, v))
    return page(f"Экономика {e(dbf_path.name)}", "\n".join(parts), extra_css=_ECON_CSS)
