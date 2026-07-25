"""Текстовый и HTML-отчёт экономики стационара.

Обе формы строятся из одного набора случаев и агрегатов (модуль economics) и
показывают одни и те же цифры: доходность койки, разбор недополученной выручки по
причинам, расшифровка КСГ. HTML самодостаточен (тема из core.report_html + правила ниже).
"""

from __future__ import annotations

import html
from collections import defaultdict
from datetime import datetime

from omsreg.core import money
from omsreg.core.report_html import page, tile
from omsreg.utils._shared.stat_common import DAY_TYPE, ROUND_TYPE, kotd_name
from omsreg.utils.stat_economics import (
    PREFIX_LABEL,
    TYPE_SHORT,
    _type_order,
    base_rates_by_type,
    cause_breakdown,
    coef_str,
    dept_rows,
    dx_examples,
    ishod_rows,
    ishod_word,
    koef_counts,
    kpr_reason,
    kpr_values,
    ksg_rows,
    type_rows,
)

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


def _trunc(s: str, n: int) -> str:
    """Обрезает строку до n символов с многоточием (для колонок фикс. ширины в txt)."""
    s = s or ""
    return s if len(s) <= n else s[: n - 1] + "…"


def build_report(dbf_path, cases, deleted, day_kotd, avail, names=None):
    out = []
    w = out.append
    n = len(cases)
    total = sum(c["stoim"] for c in cases)
    total_under = sum(c["underpaid"] for c in cases)
    full_n = sum(1 for c in cases if not c["interrupted"])
    has_kpr = avail["koef_pr"] is not None
    has_fact = avail["fact"] is not None
    has_g = avail["gruppa"] is not None
    has_full_model = has_g and all(avail[k] for k in ("koef_z", "koef_up", "koef_pr"))

    def m(x):
        return money(x) if x is not None else "—"

    def d1(x):
        return f"{x:.1f}" if x is not None else "—"

    rows = dept_rows(cases)
    krows = ksg_rows(cases) if has_g else []
    groups_with_full = {k["g"] for k in krows if k["has_full"]}
    causes = cause_breakdown(cases, groups_with_full) if has_kpr else []
    reserve = sum(c["underpaid"] for c in causes[0][1]) if causes else 0.0  # первая строка = возвратно

    # ---------- шапка ----------
    w("=" * 104)
    w("ЭКОНОМИКА И ЭФФЕКТИВНОСТЬ СТАЦИОНАРА (ОМС, оплата по КСГ)")
    w(f"Сформировано: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")
    head = f"Случаев: {n}"
    if deleted:
        head += f" (исключено удалённых: {deleted})"
    head += f"; полностью оплачено {full_n}; оплачено {money(total)} ₽"
    if has_kpr:
        head += f"; недополучено {money(total_under)} ₽"
    w(head)
    w("=" * 104)

    # ---------- главное ----------
    w("")
    w("ГЛАВНОЕ")
    ranked = [r for r in rows if r["per_day"]]
    if ranked:
        w(f"  • Лучшая по доходности койка: {kotd_name(ranked[0]['kotd'], names)} — "
          f"{money(ranked[0]['per_day'])} ₽/койко-день.")
    if rows:
        worst = max(rows, key=lambda r: r["under"])
        if worst["under"]:
            w(f"  • Больше всех недополучено: {kotd_name(worst['kotd'], names)} — "
              f"{money(worst['under'])} ₽ (полных {worst['full_pct']:.0f}%).")
    if has_kpr and total:
        w(f"  • Недополучено из-за сниженной оплаты {money(total_under)} ₽ ({n - full_n} случаев из {n}).")
        if has_fact and has_g:
            w(f"    Реально вернуть длительностью лечения можно ≈{money(reserve)} ₽ "
              f"(+{reserve / total * 100:.1f}% к оплате) — это короткие случаи 1–3 дня в группах,")
            w("    где есть полностью оплаченные случаи. Остальное длительностью не возвращается")
            w("    (переводы, прерванные по правилам КСГ, группы без полных случаев) — см. ниже.")

    # ---------- по типам стационара ----------
    w("")
    w("ПО ТИПАМ СТАЦИОНАРА")
    w(f"  {'тип':<26}{'случаев':>8}{'оплачено':>14}{'₽/к-день':>11}"
      f"{'ср.дни':>7}{'% полных':>10}{'недополуч.':>13}")
    for t in type_rows(cases):
        fp = f"{t['full_pct']:.0f}%"
        under = money(t["under"]) if t["under"] else "—"
        w(f"  {t['type']:<26}{t['n']:>8}{money(t['sum']):>14}{m(t['per_day']):>11}"
          f"{d1(t['days']):>7}{fp:>10}{under:>13}")

    # ---------- отделения (сгруппированы по типу) ----------
    w("")
    w("КАК ОТРАБОТАЛИ ОТДЕЛЕНИЯ (по типам стационара, внутри — по доходности койки)")
    w("  «₽/к-день» — вся оплата отделения ÷ его койко-дни; «% полн.» — доля полностью оплаченных случаев.")
    w(f"  {'отделение':<26}{'тип':>8}{'случаев':>8}{'оплачено':>14}{'₽/к-день':>11}"
      f"{'ср.дни':>7}{'% полн.':>9}{'недополуч.':>13}")
    for r in rows:
        fp = f"{r['full_pct']:.0f}%"
        under = money(r["under"]) if r["under"] else "—"
        w(f"  {kotd_name(r['kotd'], names):<26}{TYPE_SHORT[r['type']]:>8}{r['n']:>8}{money(r['sum']):>14}"
          f"{m(r['per_day']):>11}{d1(r['days']):>7}{fp:>9}{under:>13}")

    # ---------- где недополучено (по типам стационара) ----------
    if has_kpr:
        w("")
        w("ГДЕ НЕДОПОЛУЧЕНО И ЧТО ИЗ ЭТОГО МОЖНО ВЕРНУТЬ")
        w("  Сниженная оплата бывает по разным причинам; длительностью лечения устраняется только часть")
        w("  (строка «в группе есть полные случаи»). Разбивка по типам стационара:")
        for st in (ROUND_TYPE, DAY_TYPE):
            st_cases = [c for c in cases if c["type"] == st]
            st_under = sum(c["underpaid"] for c in st_cases)
            if not st_under:
                continue
            st_int = sum(1 for c in st_cases if c["interrupted"])
            w("")
            w(f"  {st} — недополучено {money(st_under)} ₽:")
            w(f"    {'причина':<48}{'случаев':>8}{'недополуч.':>14}   вернуть длительностью?")
            for label, group, note in cause_breakdown(st_cases, groups_with_full):
                if group:
                    w(f"    {label:<48}{len(group):>8}"
                      f"{money(sum(c['underpaid'] for c in group)):>14}   {note}")
            w(f"    {'ИТОГО':<48}{st_int:>8}{money(st_under):>14}")

        # короткие случаи по группам (с типом стационара)
        if has_fact and has_g:
            short_g = sorted([k for k in krows if k["short_n"]],
                             key=lambda k: (_type_order(k["type"]), -k["short_lost"]))
            if short_g:
                w("")
                w("  Короткие случаи (1–3 дня) по группам КСГ:")
                w("    «полные?»: «да» — случай короче нормы группы, можно довести до полной оплаты;")
                w("    «нет» — в файле нет полных случаев этой группы, поэтому по данным нельзя сказать,")
                w("    даёт ли большая длительность полную оплату; нужно свериться с правилами КСГ.")
                w(f"    {'КСГ':<12}{'тип':>8}{'случаев':>8}{'дни':>8}{'недополуч.':>14}{'полные?':>9}")
                for k in short_g[:15]:
                    days = ", ".join(str(d) for d in k["short_days"])
                    hf = "да" if k["has_full"] else "нет"
                    w(f"    {k['g']:<12}{TYPE_SHORT[k['type']]:>8}{k['short_n']:>8}{days:>8}"
                      f"{money(k['short_lost']):>14}{hf:>9}")
                w(f"    {'ИТОГО':<12}{'':>8}{sum(k['short_n'] for k in short_g):>8}{'':>8}"
                  f"{money(sum(k['short_lost'] for k in short_g)):>14}")

    # ---------- как исход влияет на оплату (по типам стационара и отделениям) ----------
    if has_kpr and avail["ishod"]:
        w("")
        w("КАК ИСХОД ВЛИЯЕТ НА ОПЛАТУ (по типам стационара и отделениям)")
        w("  У каких исходов чаще снижается оплата (по данным файла; точные правила — в КСГ).")

        def ishod_block(subset, indent):
            w(f"{indent}{'исход':<36}{'случаев':>8}{'полных':>9}{'сниженных':>11}{'недополуч.':>14}")
            for r in ishod_rows(subset):
                w(f"{indent}{ishod_word(r['ishod']):<36}{r['n']:>8}{r['full']:>9}{r['reduced']:>11}"
                  f"{money(r['under']):>14}")

        for st in (ROUND_TYPE, DAY_TYPE):
            st_cases = [c for c in cases if c["type"] == st]
            if not st_cases:
                continue
            by_k = defaultdict(list)
            for c in st_cases:
                by_k[c["kotd"]].append(c)
            w("")
            w(f"  {st}:")
            ishod_block(st_cases, "    ")
            if len(by_k) > 1:   # если отделение одно, таблица типа = таблице отделения — не дублируем
                for kotd in sorted(by_k, key=lambda k: (k is None, str(k))):
                    w("")
                    w(f"    отделение {kotd_name(kotd, names)}:")
                    ishod_block(by_k[kotd], "      ")

    # ---------- как формируется оплата случая (расшифровка формулы) ----------
    if has_full_model:
        bs = base_rates_by_type(cases)
        bs_parts = [f"{PREFIX_LABEL.get(p, p)} ~{money(v)} ₽"
                    for p, v in sorted(bs.items(), key=lambda kv: -kv[1])]
        kz_all = [k["kz_range"] for k in krows if k["kz_range"]]
        up_str = ", ".join(f"×{v:g} — {c}" for v, c in koef_counts(cases, "kup"))
        pr_str = ", ".join(f"×{v:g} — {c}" for v, c in koef_counts(cases, "kpr", reverse=True))
        w("")
        w("КАК ФОРМИРУЕТСЯ ОПЛАТА СЛУЧАЯ")
        w("  Оплата идёт за случай целиком (не за день) и равна произведению четырёх множителей:")
        w("")
        w("    оплата = базовая ставка × вес группы КСГ × поправочный коэффициент × коэффициент оплаты")
        w("")
        if bs_parts:
            w("  • Базовая ставка — единая стоимость случая для типа КСГ, восстановлена из данных:")
            w("    " + "; ".join(bs_parts) + ".")
            w("    (Тип КСГ по коду st…/ds… — не то же, что тип стационара по отделению в других разделах.)")
        if kz_all:
            lo = min(r[0] for r in kz_all)
            hi = max(r[1] for r in kz_all)
            w("  • Вес группы КСГ (столбец «вес КСГ») — во сколько раз группа дороже базовой ставки;")
            w(f"    постоянен внутри группы, в файле {lo:g}–{hi:g}.")
        w("  • Поправочный коэффициент (столбец «попр.коэф.») — задаётся по случаю. Значения в файле")
        w("    (случаев): " + up_str + ".")
        w("    Это не «уровень отделения» (в одном отделении бывают разные значения); точный смысл —")
        w("    в правилах КСГ и тарифном соглашении, из выгрузки не определяется. В этом файле он")
        w("    постоянен внутри каждой группы КСГ, поэтому показан в таблице отдельным столбцом.")
        w("  • Коэффициент оплаты — полнота случая: чем меньше, тем сильнее снижена оплата.")
        w("    Значения в файле (случаев): " + pr_str + ".")
        w("    Разбор недополученных сумм — в разделе «Где недополучено».")

    # ---------- КСГ: что за группы ----------
    if has_g:
        w("")
        w("КСГ: ЧТО ЭТО ЗА ГРУППЫ И СКОЛЬКО ПРИНОСЯТ (по типам стационара, топ по обороту)")
        w("  Группа подписана официальным наименованием и профилем из справочника КСГ, плюс реальные")
        w("  диагнозы (коды МКБ) из этого файла.")
        w("  «вес КСГ» и «попр.коэф.» — множители группы (см. блок выше); «тариф полн.» = оплата за")
        w("  полностью пролеченный случай (базовая ставка × вес × попр.коэф.); «мин.полн.день» =")
        w("  минимальная длительность, при которой была полная оплата («—» — полных случаев нет).")
        for st in (ROUND_TYPE, DAY_TYPE):
            st_ksg = sorted([k for k in krows if k["type"] == st], key=lambda k: -k["sum"])[:12]
            if not st_ksg:
                continue
            w("")
            w(f"  {st}:")
            w(f"    {'КСГ':<11}{'наименование КСГ':<34}{'профиль':<18}{'случаев':>8}{'вес КСГ':>9}"
              f"{'попр.коэф.':>11}{'тариф полн.':>14}{'мин.полн.день':>14}  диагнозы (МКБ)")
            for k in st_ksg:
                name = _trunc(k["title"] or k["chapter"], 33)
                prof = _trunc(k["profile"], 17)
                mfd = f"{k['min_full_day']:.0f}" if k["min_full_day"] is not None else "—"
                w(f"    {k['g']:<11}{name:<34}{prof:<18}{k['n']:>8}{coef_str(k['kz'], k['kz_range']):>9}"
                  f"{coef_str(k['kup'], k['kup_range']):>11}{m(k['full_tariff']):>14}"
                  f"{mfd:>14}  {dx_examples(k['dx'], 2)}")

    # ---------- методика ----------
    w("")
    w("=" * 104)
    w("МЕТОДИКА")
    w("  • Оплата идёт за случай по группе КСГ, а не за день: сумма зависит от веса группы КСГ,")
    w("    поправочного коэффициента и коэффициента оплаты (полноты пролеченного случая).")
    if has_kpr:
        w("  • Коэффициент оплаты: 1.0 = случай оплачен полностью; меньше 1 = снижена (короткий случай,")
        w("    перевод, смерть, самовольный уход, длительность ниже нормы группы). Значения в файле:")
        for kpr in kpr_values(cases):
            cs = [c for c in cases if c["kpr"] == kpr]
            if kpr >= 1:
                w(f"      ×{kpr:g} — {len(cs)} случаев (полная оплата)")
            else:
                lost = sum(c["underpaid"] for c in cs)
                w(f"      ×{kpr:g} — {len(cs)}, недополучено {money(lost)} ₽ ({kpr_reason(cs)})")
    w("  • ₽/койко-день = вся оплата отделения ÷ все его койко-дни (характеризует оборот койки).")
    day_depts = sorted(str(k) for k in {c["kotd"] for c in cases
                                        if c["type"] == DAY_TYPE and c["kotd"] is not None})
    if day_depts:
        w(f"  • Дневной/круглосуточный — по отделениям (KOTD): дневные — {', '.join(day_depts)}; "
          "остальные — круглосуточные.")
    if has_full_model:
        bs = base_rates_by_type(cases)
        pr = [f"КСГ {p}… ~{money(bs[p])} ₽" for p in ("st", "ds") if p in bs]
        if pr:
            w("  • Базовая ставка (восстановлена из данных): " + "; ".join(pr) + ".")
    w(f"  Источник данных: {dbf_path}.")
    w("=" * 104)
    return out


def build_html(dbf_path, cases, deleted, avail, names=None) -> str:
    e = html.escape
    n = len(cases)
    total = sum(c["stoim"] for c in cases)
    total_under = sum(c["underpaid"] for c in cases)
    full_n = sum(1 for c in cases if not c["interrupted"])
    has_kpr = avail["koef_pr"] is not None
    has_fact = avail["fact"] is not None
    has_g = avail["gruppa"] is not None
    has_full_model = has_g and all(avail[k] for k in ("koef_z", "koef_up", "koef_pr"))
    parts = []
    p = parts.append

    def m(x):
        return money(x) if x is not None else "—"

    def d1(x):
        return f"{x:.1f}" if x is not None else "—"

    rows = dept_rows(cases)
    krows = ksg_rows(cases) if has_g else []
    groups_with_full = {k["g"] for k in krows if k["has_full"]}
    causes = cause_breakdown(cases, groups_with_full) if has_kpr else []
    reserve = sum(c["underpaid"] for c in causes[0][1]) if causes else 0.0

    p("<h1>Экономика и эффективность стационара</h1>")
    meta = f'Сформировано {datetime.now().strftime("%d.%m.%Y %H:%M")} · случаев: {n}'
    if deleted:
        meta += f' (исключено удалённых: {deleted})'
    meta += f' · полностью оплачено: {full_n}'
    p(f'<div class="meta">{meta}</div>')

    # ---------- карточки ----------
    p('<div class="tiles">')
    p(tile(f"{money(total)} ₽", "оплачено всего"))
    if has_kpr:
        p(tile(f"{money(total_under)} ₽", "недополучено из-за сниженной оплаты", "bad"))
        if has_fact and has_g and total:
            p(tile(f"{money(reserve)} ₽",
                   f"реально вернуть длительностью (+{reserve / total * 100:.1f}%)", "good"))
    if rows and rows[0]["per_day"]:
        p(tile(e(kotd_name(rows[0]["kotd"], names)),
               f'лучшая койка · {money(rows[0]["per_day"])} ₽/день'))
    p('</div>')

    # ---------- по типам стационара ----------
    p('<h2>По типам стационара</h2>'
      '<table><thead><tr><th>тип</th><th class="num">случаев</th><th class="num">оплачено</th>'
      '<th class="num">₽/койко-день</th><th class="num">ср. дни</th><th class="num">% полных</th>'
      '<th class="num">недополуч.</th></tr></thead><tbody>')
    for t in type_rows(cases):
        under = money(t["under"]) if t["under"] else ""
        p(f'<tr><td>{e(t["type"])}</td><td class="num">{t["n"]}</td>'
          f'<td class="num">{money(t["sum"])}</td><td class="num">{m(t["per_day"])}</td>'
          f'<td class="num">{d1(t["days"])}</td><td class="num">{t["full_pct"]:.0f}%</td>'
          f'<td class="num bad">{under}</td></tr>')
    p('</tbody></table>')

    # ---------- отделения (сгруппированы по типу) ----------
    p('<h2>Как отработали отделения</h2>'
      '<p class="note">По типам стационара, внутри — по доходности койки (₽ на койко-день = '
      'оплата ÷ койко-дни). «% полных» — доля полностью оплаченных случаев.</p>'
      '<table><thead><tr><th>отделение</th><th>тип</th><th class="num">случаев</th>'
      '<th class="num">оплачено</th><th class="num">₽/койко-день</th><th class="num">ср. дни</th>'
      '<th class="num">% полных</th><th class="num">недополуч.</th></tr></thead><tbody>')
    maxpd = max((r["per_day"] or 0 for r in rows), default=0)
    for r in rows:
        pdv = r["per_day"] or 0
        width = (pdv / maxpd * 100) if maxpd else 0
        bar = (f'<div class="barwrap"><div class="bar" style="width:{width:.0f}%"></div>'
               f'<span>{m(r["per_day"])}</span></div>')
        fullcls = "good" if r["full_pct"] >= 85 else ("warn" if r["full_pct"] >= 70 else "bad")
        under = money(r["under"]) if r["under"] else ""
        p(f'<tr><td>{e(kotd_name(r["kotd"], names))}</td><td>{e(TYPE_SHORT[r["type"]])}</td>'
          f'<td class="num">{r["n"]}</td><td class="num">{money(r["sum"])}</td>'
          f'<td class="num">{bar}</td><td class="num">{d1(r["days"])}</td>'
          f'<td class="num {fullcls}">{r["full_pct"]:.0f}%</td><td class="num bad">{under}</td></tr>')
    p('</tbody></table>')

    # ---------- где недополучено (по типам стационара) ----------
    if has_kpr:
        p('<h2>Где недополучено и что из этого можно вернуть</h2>'
          '<p class="note">Сниженная оплата бывает по разным причинам; длительностью лечения '
          'устраняется только строка «в группе есть полные случаи». Разбивка по типам стационара:</p>')
        for st in (ROUND_TYPE, DAY_TYPE):
            st_cases = [c for c in cases if c["type"] == st]
            st_under = sum(c["underpaid"] for c in st_cases)
            if not st_under:
                continue
            st_int = sum(1 for c in st_cases if c["interrupted"])
            p(f'<h3 style="font-size:15px;margin:10px 0 4px">{e(st)} — недополучено {money(st_under)} ₽</h3>'
              '<table><thead><tr><th>причина</th><th class="num">случаев</th>'
              '<th class="num">недополуч.</th><th>вернуть длительностью?</th></tr></thead><tbody>')
            for label, group, note in cause_breakdown(st_cases, groups_with_full):
                if group:
                    cls = "good" if note.startswith("да") else "warn"
                    p(f'<tr><td>{e(label)}</td><td class="num">{len(group)}</td>'
                      f'<td class="num bad">{money(sum(c["underpaid"] for c in group))}</td>'
                      f'<td class="{cls}">{e(note)}</td></tr>')
            p(f'<tr class="total"><td>ИТОГО</td><td class="num">{st_int}</td>'
              f'<td class="num bad">{money(st_under)}</td><td></td></tr></tbody></table>')

        if has_fact and has_g:
            short_g = sorted([k for k in krows if k["short_n"]],
                             key=lambda k: (_type_order(k["type"]), -k["short_lost"]))
            if short_g:
                p('<h3 style="font-size:15px;margin:12px 0 4px">Короткие случаи (1–3 дня) по группам КСГ</h3>'
                  '<p class="note">«Полные в группе»: <b>да</b> — случай короче нормы группы, можно довести '
                  'до полной оплаты. <b>Нет</b> — в файле нет полных случаев этой группы, поэтому по данным '
                  'нельзя сказать, даёт ли большая длительность полную оплату; нужно свериться с правилами КСГ.</p>'
                  '<table><thead><tr><th>КСГ</th><th>тип</th><th class="num">случаев</th><th class="num">дни</th>'
                  '<th class="num">недополуч.</th><th class="num">полные?</th></tr></thead><tbody>')
                for k in short_g[:15]:
                    days = ", ".join(str(d) for d in k["short_days"])
                    hf = ('<span class="good">да</span>' if k["has_full"]
                          else '<span class="warn">нет</span>')
                    p(f'<tr><td><b>{e(k["g"])}</b></td><td>{e(TYPE_SHORT[k["type"]])}</td>'
                      f'<td class="num">{k["short_n"]}</td>'
                      f'<td class="num">{e(days)}</td><td class="num bad">{money(k["short_lost"])}</td>'
                      f'<td class="num">{hf}</td></tr>')
                p(f'<tr class="total"><td>ИТОГО</td><td></td>'
                  f'<td class="num">{sum(k["short_n"] for k in short_g)}</td>'
                  f'<td></td><td class="num bad">{money(sum(k["short_lost"] for k in short_g))}</td>'
                  f'<td></td></tr></tbody></table>')

    # ---------- как исход влияет на оплату (по типам стационара и отделениям) ----------
    if has_kpr and avail["ishod"]:
        def ishod_table_html(subset):
            p('<table><thead><tr><th>исход</th><th class="num">случаев</th><th class="num">полных</th>'
              '<th class="num">сниженных</th><th class="num">недополуч.</th></tr></thead><tbody>')
            for r in ishod_rows(subset):
                under = money(r["under"]) if r["under"] else ""
                p(f'<tr><td>{e(ishod_word(r["ishod"]))}</td><td class="num">{r["n"]}</td>'
                  f'<td class="num good">{r["full"]}</td><td class="num">{r["reduced"]}</td>'
                  f'<td class="num bad">{under}</td></tr>')
            p('</tbody></table>')

        p('<h2>Как исход влияет на оплату</h2>'
          '<p class="note">По типам стационара и отделениям. У каких исходов чаще снижается оплата '
          '(по данным файла; точные правила — в КСГ).</p>')
        for st in (ROUND_TYPE, DAY_TYPE):
            st_cases = [c for c in cases if c["type"] == st]
            if not st_cases:
                continue
            by_k = defaultdict(list)
            for c in st_cases:
                by_k[c["kotd"]].append(c)
            p(f'<h3 style="font-size:15px;margin:12px 0 4px">{e(st)}</h3>')
            ishod_table_html(st_cases)
            if len(by_k) > 1:   # одно отделение — таблица типа совпадает с отделением, не дублируем
                for kotd in sorted(by_k, key=lambda k: (k is None, str(k))):
                    p('<h4 style="font-size:13.5px;margin:8px 0 2px;color:var(--ink2)">'
                      f'отделение {e(kotd_name(kotd, names))}</h4>')
                    ishod_table_html(by_k[kotd])

    # ---------- как формируется оплата случая ----------
    if has_full_model:
        bs = base_rates_by_type(cases)
        bs_parts = [f'{e(PREFIX_LABEL.get(pf, pf))} ~{money(v)} ₽'
                    for pf, v in sorted(bs.items(), key=lambda kv: -kv[1])]
        kz_all = [k["kz_range"] for k in krows if k["kz_range"]]
        kzr = (f'{min(r[0] for r in kz_all):g}–{max(r[1] for r in kz_all):g}' if kz_all else '—')
        up_str = ", ".join(f"×{v:g} — {c}" for v, c in koef_counts(cases, "kup"))
        pr_str = ", ".join(f"×{v:g} — {c}" for v, c in koef_counts(cases, "kpr", reverse=True))
        p('<h2>Как формируется оплата случая</h2>')
        p('<p style="font-size:15px;margin:6px 0"><b>оплата = базовая ставка × вес группы КСГ × '
          'поправочный коэффициент × коэффициент оплаты</b></p>')
        p('<table><thead><tr><th>множитель</th><th>что показывает</th><th>постоянен</th>'
          '<th>значения в файле</th></tr></thead><tbody>')
        p(f'<tr><td><b>Базовая ставка</b></td><td>единая стоимость случая, восстановлена из данных; '
          f'подписывается кодом КСГ (st…/ds…), а не «круглосуточный/дневной»</td>'
          f'<td>тип КСГ (код st…/ds…)</td><td>{"; ".join(bs_parts) or "—"}</td></tr>')
        p(f'<tr><td><b>Вес группы КСГ</b> (столбец «вес КСГ»)</td>'
          f'<td>во сколько раз группа дороже базовой ставки</td><td>группа КСГ</td>'
          f'<td>{e(kzr)}</td></tr>')
        p(f'<tr><td><b>Поправочный коэффициент</b> (столбец «попр. коэф.»)</td>'
          f'<td>корректирует стоимость; это <b>не «уровень отделения»</b> — точный смысл в правилах '
          f'КСГ / тарифном соглашении, из данных не определяется</td>'
          f'<td>по случаю (в этом файле — один на группу КСГ)</td>'
          f'<td>{e(up_str)}</td></tr>')
        p(f'<tr><td><b>Коэффициент оплаты</b></td>'
          f'<td>полнота случая: 1.0 — оплачен полностью, меньше 1 — снижена (см. «Где недополучено»)</td>'
          f'<td>по случаю</td><td>{e(pr_str)}</td></tr>')
        p('</tbody></table>')
        p('<p class="note"><b>Тариф полн. случая</b> в таблице ниже = базовая ставка × вес × '
          'поправочный коэффициент — оплата за полностью пролеченный случай.</p>')

    # ---------- КСГ (по типам стационара) ----------
    if has_g:
        p('<h2>КСГ: что это за группы и сколько приносят</h2>'
          '<p class="note">По типам стационара, топ по обороту. Группа подписана официальным '
          'наименованием и профилем из справочника КСГ (полное название — во всплывающей подсказке '
          'при наведении) и реальными диагнозами (МКБ) из этого файла. «вес КСГ» и «попр. коэф.» — '
          'множители группы (см. блок выше); «тариф полн. случая» = оплата за полностью пролеченный '
          'случай; «мин. полный день» = минимальная длительность, при которой была полная оплата. '
          '«—» — полных случаев в группе нет.</p>')
        for st in (ROUND_TYPE, DAY_TYPE):
            st_ksg = sorted([k for k in krows if k["type"] == st], key=lambda k: -k["sum"])[:12]
            if not st_ksg:
                continue
            p(f'<h3 style="font-size:15px;margin:10px 0 4px">{e(st)}</h3>'
              '<table><thead><tr><th>КСГ</th><th>наименование</th><th>профиль</th>'
              '<th class="num">случаев</th><th class="num">вес КСГ</th><th class="num">попр. коэф.</th>'
              '<th class="num">тариф полн. случая, ₽</th><th class="num">мин. полный день</th>'
              '<th>диагнозы (примеры)</th></tr></thead><tbody>')
            for k in st_ksg:
                mfd = f"{k['min_full_day']:.0f}" if k["min_full_day"] is not None else "—"
                name = k["title"] or k["chapter"]
                p(f'<tr><td><b>{e(k["g"])}</b></td>'
                  f'<td class="ksg-name" title="{e(name)}">{e(name)}</td>'
                  f'<td class="ksg-prof" title="{e(k["profile"])}">{e(k["profile"])}</td>'
                  f'<td class="num">{k["n"]}</td><td class="num">{e(coef_str(k["kz"], k["kz_range"]))}</td>'
                  f'<td class="num">{e(coef_str(k["kup"], k["kup_range"]))}</td>'
                  f'<td class="num">{m(k["full_tariff"])}</td><td class="num">{mfd}</td>'
                  f'<td class="dx">{e(dx_examples(k["dx"]))}</td></tr>')
            p('</tbody></table>')

    # ---------- методика ----------
    p('<h2>Методика</h2><ul class="note main-list">')
    p('<li>Оплата идёт за случай по группе КСГ, а не за день: сумма зависит от веса группы КСГ, '
      'поправочного коэффициента и коэффициента оплаты (полноты пролеченного случая).</li>')
    if has_kpr:
        items = []
        for kpr in kpr_values(cases):
            cs = [c for c in cases if c["kpr"] == kpr]
            if kpr >= 1:
                items.append(f"×{kpr:g} — {len(cs)} случаев (полная оплата)")
            else:
                lost = sum(c["underpaid"] for c in cs)
                items.append(f"×{kpr:g} — {len(cs)}, недополучено {money(lost)} ₽ ({e(kpr_reason(cs))})")
        p('<li>Коэффициент оплаты: 1.0 — полностью, меньше 1 — снижена (короткий случай, перевод, '
          'смерть, самовольный уход, длительность ниже нормы группы). Значения в файле: '
          + "; ".join(items) + '.</li>')
    p('<li>₽/койко-день = вся оплата отделения ÷ все его койко-дни (оборот койки).</li>')
    day_depts = sorted(str(k) for k in {c["kotd"] for c in cases
                                        if c["type"] == DAY_TYPE and c["kotd"] is not None})
    if day_depts:
        p(f'<li>Дневной/круглосуточный — по отделениям (KOTD): дневные — {e(", ".join(day_depts))}; '
          'остальные — круглосуточные.</li>')
    p(f'<li>Источник данных: {e(dbf_path.name)}.</li>')
    p('</ul>')

    return page(f"Экономика {e(dbf_path.name)}", "\n".join(parts), extra_css=_ECON_CSS)
