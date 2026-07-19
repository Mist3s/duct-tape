#!/usr/bin/env python3
"""Экономика и эффективность стационара по DBF-реестру ОМС (оплата по КСГ).

Управленческий отчёт «как отработали и где резерв»: доходность койки (₽ на
койко-день), честная разбивка недополученной выручки по причинам (что реально
вернуть длительностью лечения, а что нет), расшифровка групп КСГ диагнозами из
самого файла. Все числа и коэффициенты берутся динамически из файла.

Оплата идёт ЗА СЛУЧАЙ по группе КСГ (не за день) и равна:
    базовая ставка × вес группы КСГ × уровень отделения × коэффициент оплаты.
Коэффициент оплаты: 1.0 — случай оплачен полностью, меньше 1 — оплата снижена
(короткий случай, перевод, смерть/самовольный уход, длительность ниже норматива
группы). «Недополучено» = полная сумма минус фактическая.

В полях DBF это: STOIM (стоимость случая), KOEF_Z (вес КСГ), KOEF_UP (уровень),
KOEF_PR (коэффициент оплаты), GRUPPA (код КСГ), FACT (койко-дни), ISHOD (исход).
Логика расчёта собрана в разделе «модель оплаты» ниже — правьте там при смене модели.

Примеры запуска:
    omsreg-econ data/0091_016.dbf
    omsreg-econ data/0091_016.dbf --day-kotd 10,15,12
"""

from __future__ import annotations

import argparse
import html
import logging
import sys
from collections import Counter, defaultdict
from datetime import datetime
from statistics import median

from omsreg.core import DbfTable, JobError, as_float, as_int, setup_job_logging
from omsreg.utils.stat_stacionar import (
    DAY_TYPE,
    ISHOD_NAMES,
    ROUND_TYPE,
    kotd_name,
    money,
    parse_day_kotd,
    parse_kotd_names,
    resolve_dbf_path,
)

log = logging.getLogger("omsreg.utils.stat_economics")

# имена полей DBF по умолчанию (можно переопределить в интерфейсе/через run_economics)
ECON_FIELDS = {
    "kotd": "KOTD", "kmkb": "KMKB", "stoim": "STOIM", "fact": "FACT", "ishod": "ISHOD",
    "gruppa": "GRUPPA", "koef_z": "KOEF_Z", "koef_up": "KOEF_UP", "koef_pr": "KOEF_PR",
}

TRANSFER_ISHOD = 5  # исход «перевод»

# короткие подписи типа стационара и порядок вывода (круглосуточный первым)
TYPE_SHORT = {ROUND_TYPE: "кругл.", DAY_TYPE: "дневн."}


def _type_order(t) -> int:
    return 0 if t == ROUND_TYPE else 1

# главы МКБ по первой букве кода — чтобы пояснить, что за диагнозы в группе КСГ
MKB_CHAPTERS = {
    "A": "инфекции", "B": "инфекции", "C": "новообразования", "D": "кровь/новообразования",
    "E": "эндокринные", "F": "психические", "G": "нервная система", "H": "глаз/ухо",
    "I": "система кровообращения", "J": "органы дыхания", "K": "пищеварение", "L": "кожа",
    "M": "костно-мышечная", "N": "мочеполовая", "O": "беременность", "P": "перинатальные",
    "Q": "врождённые аномалии", "R": "симптомы/признаки", "S": "травмы", "T": "травмы/отравления",
    "Z": "факторы обращения",
}


def mkb_chapter(code: str) -> str:
    return MKB_CHAPTERS.get((code or "")[:1].upper(), "")


def ishod_word(code) -> str:
    """Название исхода словами (без числового кода). None -> 'исход не указан'."""
    if code is None:
        return "исход не указан"
    return ISHOD_NAMES.get(code, f"исход {code}")


# ----------------------------- модель оплаты (правьте здесь при смене логики) -----------------------------

def base_rate(stoim, koef_z, koef_up, koef_pr):
    """Базовая ставка = стоимость / (вес × уровень × коэффициент оплаты). None, если данных нет."""
    if not (stoim and koef_z and koef_up and koef_pr):
        return None
    return stoim / (koef_z * koef_up * koef_pr)


def full_payment(stoim, koef_pr):
    """Сколько стоил бы случай при полной оплате (коэффициент 1). Для полных == сама стоимость."""
    if koef_pr and koef_pr > 0:
        return stoim / koef_pr
    return stoim


def underpaid(stoim, koef_pr):
    """Недополучено из-за сниженной оплаты (полная сумма минус фактическая)."""
    if koef_pr is not None and 0 < koef_pr < 1:
        return full_payment(stoim, koef_pr) - stoim
    return 0.0


def ksg_prefix(gruppa: str) -> str:
    """Префикс кода КСГ: 'st' — круглосуточный, 'ds' — дневной стационар, иначе '?'."""
    g = (gruppa or "").strip().lower()
    return g[:2] if g[:2] in ("st", "ds") else "?"


# ----------------------------- сбор данных -----------------------------

def collect(table: DbfTable, day_kotd, fields=None):
    """Собирает случаи со стоимостными полями. Возвращает (список dict-ов, deleted, доступные поля)."""
    f = dict(ECON_FIELDS)
    if fields:
        f.update({k: str(v).strip().upper() for k, v in fields.items() if str(v).strip()})
    if not table.has_field(f["stoim"]):
        have = ", ".join(fld.name for fld in table.fields)
        raise ValueError(f"в файле нет поля стоимости {f['stoim']} (есть: {have})")
    avail = {k: (f[k] if table.has_field(f[k]) else None) for k in f}

    cases, deleted = [], 0
    for rec in table.records:
        if table.is_deleted(rec):
            deleted += 1
            continue

        def val(key, rec=rec):
            return table.value(rec, avail[key]) if avail[key] else ""

        stoim = as_float(val("stoim")) or 0.0
        kotd = as_int(val("kotd")) if avail["kotd"] else None
        fact = as_float(val("fact")) if avail["fact"] else None
        ishod = as_int(val("ishod")) if avail["ishod"] else None
        kpr = as_float(val("koef_pr")) if avail["koef_pr"] else None
        cases.append({
            "type": DAY_TYPE if kotd in day_kotd else ROUND_TYPE,
            "kotd": kotd, "fact": fact, "kmkb": val("kmkb") or "(без МКБ)",
            "ishod": ishod, "gruppa": val("gruppa").strip(),
            "kz": as_float(val("koef_z")) if avail["koef_z"] else None,
            "kup": as_float(val("koef_up")) if avail["koef_up"] else None,
            "kpr": kpr, "stoim": stoim, "underpaid": underpaid(stoim, kpr),
            "interrupted": kpr is not None and kpr < 1,
        })
    return cases, deleted, avail


# ----------------------------- агрегации -----------------------------

def _avg(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def _per_day(total_stoim, cases):
    days = sum(c["fact"] for c in cases if c["fact"])
    return total_stoim / days if days else None


def kpr_values(cases):
    """Различные значения коэффициента оплаты в файле, по убыванию (1.0 первым)."""
    return sorted({c["kpr"] for c in cases if c["kpr"] is not None}, reverse=True)


def kpr_reason(cs):
    """Пояснение к сниженному коэффициенту: диапазон длительности и исходы (словами)."""
    ish = Counter(c["ishod"] for c in cs)
    facts = [c["fact"] for c in cs if c["fact"] is not None]
    who = ", ".join(f"{ishod_word(i)}: {n}" for i, n in ish.most_common(3))
    days = f"дни {min(facts):.0f}–{max(facts):.0f}" if facts else ""
    return "; ".join(x for x in [days, who] if x)


def type_rows(cases):
    """Сводка по типам стационара (круглосуточный, дневной)."""
    out = []
    for st in (ROUND_TYPE, DAY_TYPE):
        cs = [c for c in cases if c["type"] == st]
        if not cs:
            continue
        s = sum(c["stoim"] for c in cs)
        full = sum(1 for c in cs if not c["interrupted"])
        out.append({
            "type": st, "n": len(cs), "sum": s, "per_day": _per_day(s, cs),
            "days": _avg([c["fact"] for c in cs]), "full_pct": full / len(cs) * 100,
            "under": sum(c["underpaid"] for c in cs),
        })
    return out


def dept_rows(cases):
    """Агрегаты по отделениям (KOTD). Сгруппированы по типу стационара, внутри — по доходности койки."""
    by = defaultdict(list)
    for c in cases:
        by[c["kotd"]].append(c)
    rows = []
    for kotd, cs in by.items():
        s = sum(c["stoim"] for c in cs)
        full = sum(1 for c in cs if not c["interrupted"])
        rows.append({
            "kotd": kotd, "type": cs[0]["type"], "n": len(cs), "sum": s, "avg": s / len(cs),
            "days": _avg([c["fact"] for c in cs]), "per_day": _per_day(s, cs),
            "full_pct": full / len(cs) * 100, "under": sum(c["underpaid"] for c in cs),
        })
    rows.sort(key=lambda r: (_type_order(r["type"]), -(r["per_day"] or 0)))
    return rows


def ksg_rows(cases):
    """Агрегаты по КСГ: оборот, доходность идеального дня, короткие случаи, диагнозы."""
    by = defaultdict(list)
    for c in cases:
        by[c["gruppa"]].append(c)
    rows = []
    for g, cs in by.items():
        full = [c for c in cs if not c["interrupted"]]
        # короткие: недооплаченные, реально короткие (≤3 дней) и не переводы
        short = [c for c in cs if c["interrupted"] and c["ishod"] != TRANSFER_ISHOD
                 and c["fact"] is not None and c["fact"] <= 3]
        s = sum(c["stoim"] for c in cs)
        chapters = Counter(mkb_chapter(c["kmkb"]) for c in cs if mkb_chapter(c["kmkb"]))
        rows.append({
            "g": g, "type": Counter(c["type"] for c in cs).most_common(1)[0][0],
            "n": len(cs), "sum": s, "avg": s / len(cs),
            "per_day": _per_day(s, cs),
            # «идеальный» койко-день: максимум стоимость÷дни среди полностью оплаченных случаев
            "ideal_per_day": max((c["stoim"] / c["fact"] for c in full if c["fact"]), default=None),
            # минимальная длительность, при которой встречалась ПОЛНАЯ оплата (None — полных нет)
            "min_full_day": min((c["fact"] for c in full if c["fact"] is not None), default=None),
            "has_full": bool(full),
            "short_n": len(short),
            "short_days": sorted({int(c["fact"]) for c in short}),
            "short_lost": sum(c["underpaid"] for c in short),
            "dx": Counter(c["kmkb"] for c in cs),
            "chapter": chapters.most_common(1)[0][0] if chapters else "",
        })
    return rows


def dx_examples(dx: Counter, limit: int = 3) -> str:
    """Примеры диагнозов из файла: 'J18.9×25, J45.8×9, J15.8×7'."""
    return ", ".join(f"{code}×{cnt}" for code, cnt in dx.most_common(limit))


def ishod_rows(cases):
    """Связь исхода с оплатой: сколько случаев, полных/сниженных, средний коэффициент, недооплата.
    Отсортировано по недополученной сумме (самые влияющие исходы сверху)."""
    by = defaultdict(list)
    for c in cases:
        by[c["ishod"]].append(c)
    rows = []
    for code, cs in by.items():
        full = sum(1 for c in cs if not c["interrupted"])
        rows.append({
            "ishod": code, "n": len(cs), "full": full, "reduced": len(cs) - full,
            "under": sum(c["underpaid"] for c in cs),
        })
    rows.sort(key=lambda r: -r["under"])
    return rows


def cause_breakdown(cases, groups_with_full):
    """Разбивка недополученной суммы по причинам. Возвращает список (подпись, случаи, возвратно?)."""
    tr = [c for c in cases if c["interrupted"] and c["ishod"] == TRANSFER_ISHOD]
    ntr = [c for c in cases if c["interrupted"] and c["ishod"] != TRANSFER_ISHOD]
    is_short = lambda c: c["fact"] is not None and c["fact"] <= 3  # noqa: E731
    short_full = [c for c in ntr if is_short(c) and c["gruppa"] in groups_with_full]
    short_nofull = [c for c in ntr if is_short(c) and c["gruppa"] not in groups_with_full]
    longred = [c for c in ntr if not is_short(c)]
    return [
        ("Короткие 1–3 дня, в группе есть полные случаи", short_full, "да — довести до нормы группы"),
        ("Короткие 1–3 дня, в группе нет полных случаев", short_nofull,
         "по данным не определить — свериться с правилами КСГ"),
        ("Прерванные ≥4 дней (не переводы)", longred, "нет — правила/норматив КСГ"),
        ("Переводы в другой стационар", tr, "нет — организационный вопрос"),
    ]


# ----------------------------- текстовый отчёт -----------------------------

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

    # ---------- как исход влияет на оплату ----------
    if has_kpr and avail["ishod"]:
        w("")
        w("КАК ИСХОД ВЛИЯЕТ НА ОПЛАТУ")
        w("  У каких исходов чаще снижается оплата (по данным файла; точные правила — в КСГ).")
        w(f"  {'исход':<38}{'случаев':>8}{'полных':>9}{'сниженных':>11}{'недополуч.':>14}")
        for r in ishod_rows(cases):
            w(f"  {ishod_word(r['ishod']):<38}{r['n']:>8}{r['full']:>9}{r['reduced']:>11}"
              f"{money(r['under']):>14}")

    # ---------- КСГ: что за группы ----------
    if has_g:
        w("")
        w("КСГ: ЧТО ЭТО ЗА ГРУППЫ И СКОЛЬКО ПРИНОСЯТ (по типам стационара, топ по обороту)")
        w("  Группа поясняется реальными диагнозами (коды МКБ) и главой МКБ из этого файла.")
        w("  «ср.случай» = оплачено ÷ число случаев; «мин.полн.день» = минимальная длительность,")
        w("  при которой встречалась полная оплата; «макс.₽/к-день» = наибольшая оплата койко-дня")
        w("  среди полностью оплаченных случаев. «—» — полных случаев в группе нет.")
        for st in (ROUND_TYPE, DAY_TYPE):
            st_ksg = sorted([k for k in krows if k["type"] == st], key=lambda k: -k["sum"])[:12]
            if not st_ksg:
                continue
            w("")
            w(f"  {st}:")
            w(f"    {'КСГ':<12}{'случаев':>8}{'ср.случай':>12}{'мин.полн.день':>15}"
              f"{'макс.₽/к-день':>15}  диагнозы (МКБ) — глава")
            for k in st_ksg:
                tail = f" — {k['chapter']}" if k["chapter"] else ""
                mfd = f"{k['min_full_day']:.0f}" if k["min_full_day"] is not None else "—"
                w(f"    {k['g']:<12}{k['n']:>8}{money(k['avg']):>12}{mfd:>15}"
                  f"{m(k['ideal_per_day']):>15}  {dx_examples(k['dx'])}{tail}")

    # ---------- методика ----------
    w("")
    w("=" * 104)
    w("МЕТОДИКА")
    w("  • Оплата идёт за случай по группе КСГ, а не за день: сумма зависит от веса группы КСГ,")
    w("    уровня отделения и коэффициента оплаты (полноты пролеченного случая).")
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
    if all(avail[k] for k in ("koef_z", "koef_up", "koef_pr", "gruppa")):
        by_pref = defaultdict(list)
        for c in cases:
            b = base_rate(c["stoim"], c["kz"], c["kup"], c["kpr"])
            if b:
                by_pref[ksg_prefix(c["gruppa"])].append(b)
        pr = []
        if by_pref.get("st"):
            pr.append(f"КСГ st… ~{money(median(by_pref['st']))} ₽")
        if by_pref.get("ds"):
            pr.append(f"КСГ ds… ~{money(median(by_pref['ds']))} ₽")
        if pr:
            w("  • Базовая ставка (восстановлена из данных): " + "; ".join(pr) + ".")
    w(f"  Источник данных: {dbf_path}.")
    w("=" * 104)
    return out


# ----------------------------- HTML-отчёт -----------------------------

_CSS = """
:root { color-scheme: light; --surface:#fcfcfb; --card:#f4f4f2; --ink:#0b0b0b; --ink2:#52514e;
  --border:#e3e2de; --accent:#2a78d6; --bar:#2a78d680; --hover:#eef3fa; --good:#2e7d32; --bad:#c23b3b; --warn:#b26a00; }
@media (prefers-color-scheme: dark) { :root { color-scheme: dark; --surface:#1a1a19; --card:#242423;
  --ink:#fff; --ink2:#c3c2b7; --border:#3a3a38; --accent:#3987e5; --bar:#3987e580; --hover:#24303f;
  --good:#7fd08a; --bad:#ef8a8a; --warn:#e0a24a; } }
* { box-sizing:border-box; } body { margin:0; padding:24px; background:var(--surface); color:var(--ink);
  font:15px/1.5 system-ui,"Segoe UI",Roboto,sans-serif; } .wrap { max-width:1120px; margin:0 auto; }
h1 { font-size:22px; margin:0 0 4px; } h2 { font-size:18px; margin:28px 0 6px; }
.meta { color:var(--ink2); font-size:13.5px; margin-bottom:14px; } .note { color:var(--ink2); font-size:13px; margin:4px 0 8px; }
.main-list { margin:8px 0 4px; padding-left:20px; } .main-list li { margin:4px 0; }
.tiles { display:flex; flex-wrap:wrap; gap:12px; margin:14px 0; }
.tile { background:var(--card); border:1px solid var(--border); border-radius:10px; padding:12px 18px; min-width:150px; }
.tile .v { font-size:22px; font-weight:700; white-space:nowrap; } .tile .l { font-size:12.5px; color:var(--ink2); margin-top:2px; }
table { border-collapse:collapse; width:100%; margin:6px 0 4px; font-size:14px; font-variant-numeric:tabular-nums; }
th { text-align:left; color:var(--ink2); font-weight:600; font-size:12.5px; border-bottom:2px solid var(--border); padding:6px 10px; white-space:nowrap; }
td { border-bottom:1px solid var(--border); padding:5px 10px; } td.num,th.num { text-align:right; white-space:nowrap; }
tbody tr:hover td { background:var(--hover); } .bad { color:var(--bad); } .good { color:var(--good); } .warn { color:var(--warn); }
.dx { color:var(--ink2); font-size:13px; } tr.total td { font-weight:700; border-top:2px solid var(--border); }
.barwrap { position:relative; min-width:110px; } .barwrap .bar { position:absolute; left:0; top:50%;
  transform:translateY(-50%); height:10px; background:var(--bar); border-radius:0 4px 4px 0; } .barwrap span { position:relative; padding-left:4px; }
@media print { body { padding:0; } }
"""


def build_html(dbf_path, cases, deleted, avail, names=None) -> str:
    e = html.escape
    n = len(cases)
    total = sum(c["stoim"] for c in cases)
    total_under = sum(c["underpaid"] for c in cases)
    full_n = sum(1 for c in cases if not c["interrupted"])
    has_kpr = avail["koef_pr"] is not None
    has_fact = avail["fact"] is not None
    has_g = avail["gruppa"] is not None
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

    p(f'<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">'
      f'<meta name="viewport" content="width=device-width, initial-scale=1">'
      f'<title>Экономика {e(dbf_path.name)}</title><style>{_CSS}</style></head><body><div class="wrap">')
    p("<h1>Экономика и эффективность стационара</h1>")
    meta = f'Сформировано {datetime.now().strftime("%d.%m.%Y %H:%M")} · случаев: {n}'
    if deleted:
        meta += f' (исключено удалённых: {deleted})'
    meta += f' · полностью оплачено: {full_n}'
    p(f'<div class="meta">{meta}</div>')

    # ---------- карточки ----------
    p('<div class="tiles">')
    p(f'<div class="tile"><div class="v">{money(total)} ₽</div><div class="l">оплачено всего</div></div>')
    if has_kpr:
        p(f'<div class="tile"><div class="v bad">{money(total_under)} ₽</div>'
          f'<div class="l">недополучено из-за сниженной оплаты</div></div>')
        if has_fact and has_g and total:
            p(f'<div class="tile"><div class="v good">{money(reserve)} ₽</div>'
              f'<div class="l">реально вернуть длительностью (+{reserve / total * 100:.1f}%)</div></div>')
    if rows and rows[0]["per_day"]:
        p(f'<div class="tile"><div class="v">{e(kotd_name(rows[0]["kotd"], names))}</div>'
          f'<div class="l">лучшая койка · {money(rows[0]["per_day"])} ₽/день</div></div>')
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

    # ---------- как исход влияет на оплату ----------
    if has_kpr and avail["ishod"]:
        p('<h2>Как исход влияет на оплату</h2>'
          '<p class="note">У каких исходов чаще снижается оплата (по данным файла; точные правила — в КСГ).</p>'
          '<table><thead><tr><th>исход</th><th class="num">случаев</th><th class="num">полных</th>'
          '<th class="num">сниженных</th><th class="num">недополуч.</th></tr></thead><tbody>')
        for r in ishod_rows(cases):
            under = money(r["under"]) if r["under"] else ""
            p(f'<tr><td>{e(ishod_word(r["ishod"]))}</td><td class="num">{r["n"]}</td>'
              f'<td class="num good">{r["full"]}</td><td class="num">{r["reduced"]}</td>'
              f'<td class="num bad">{under}</td></tr>')
        p('</tbody></table>')

    # ---------- КСГ (по типам стационара) ----------
    if has_g:
        p('<h2>КСГ: что это за группы и сколько приносят</h2>'
          '<p class="note">По типам стационара, топ по обороту. Группа поясняется реальными диагнозами '
          '(МКБ) и главой МКБ из этого файла. «ср. случай» = оплачено ÷ число случаев; '
          '«мин. полный день» = минимальная длительность, при которой встречалась полная оплата; '
          '«макс. ₽/койко-день» = наибольшая оплата койко-дня среди полностью оплаченных случаев. '
          '«—» — полных случаев в группе нет.</p>')
        for st in (ROUND_TYPE, DAY_TYPE):
            st_ksg = sorted([k for k in krows if k["type"] == st], key=lambda k: -k["sum"])[:12]
            if not st_ksg:
                continue
            p(f'<h3 style="font-size:15px;margin:10px 0 4px">{e(st)}</h3>'
              '<table><thead><tr><th>КСГ</th><th>глава МКБ</th><th class="num">случаев</th>'
              '<th class="num">ср. случай</th><th class="num">мин. полный день</th>'
              '<th class="num">макс. ₽/койко-день</th><th>диагнозы (примеры)</th></tr></thead><tbody>')
            for k in st_ksg:
                mfd = f"{k['min_full_day']:.0f}" if k["min_full_day"] is not None else "—"
                p(f'<tr><td><b>{e(k["g"])}</b></td><td>{e(k["chapter"])}</td>'
                  f'<td class="num">{k["n"]}</td><td class="num">{money(k["avg"])}</td>'
                  f'<td class="num">{mfd}</td><td class="num">{m(k["ideal_per_day"])}</td>'
                  f'<td class="dx">{e(dx_examples(k["dx"]))}</td></tr>')
            p('</tbody></table>')

    # ---------- методика ----------
    p('<h2>Методика</h2><ul class="note main-list">')
    p('<li>Оплата идёт за случай по группе КСГ, а не за день: сумма зависит от веса группы КСГ, '
      'уровня отделения и коэффициента оплаты (полноты пролеченного случая).</li>')
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

    p('</div></body></html>')
    return "\n".join(parts)


# ----------------------------- запуск -----------------------------

def run_economics(target, day_kotd="10,15,12", fields=None, kotd_names=None,
                  extra_handlers=None, console=True) -> dict:
    """Строит экономический отчёт стационара (.txt/.html рядом с DBF). -> dict с путями и итогами."""
    path = resolve_dbf_path(target)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = path.parent / f"ekonomika_{path.stem}_{ts}"
    log_path = base.with_suffix(".log")
    setup_job_logging(log, log_path, extra_handlers, console)

    log.info("Файл: %s", path)
    day_kotd_set = parse_day_kotd(day_kotd)
    try:
        table = DbfTable(path)
        log.info("Записей в файле: %d", table.nrec)
        cases, deleted, avail = collect(table, day_kotd_set, fields)
    except ValueError as e:
        log.error("%s", e)
        raise JobError(str(e)) from e
    log.info("Отобрано случаев: %d", len(cases))
    missing = [ECON_FIELDS[k] for k in ("fact", "koef_z", "koef_up", "koef_pr", "gruppa") if not avail[k]]
    if missing:
        log.warning("нет полей: %s — соответствующие разделы сокращены", ", ".join(missing))

    log.info("Считаю экономику…")
    text = "\n".join(build_report(path, cases, deleted, day_kotd_set, avail, kotd_names))
    txt_path = base.with_suffix(".txt")
    html_path = base.with_suffix(".html")
    txt_path.write_text(text + "\n", encoding="utf-8")
    html_path.write_text(build_html(path, cases, deleted, avail, kotd_names), encoding="utf-8")
    log.info("Готово. Файлы: %s, %s", txt_path.name, html_path.name)

    return {"text": text, "txt_path": txt_path, "html_path": html_path, "log_path": log_path,
            "cases": len(cases), "total": sum(c["stoim"] for c in cases),
            "underpaid": sum(c["underpaid"] for c in cases)}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Экономика и эффективность стационара по DBF (доходность койки, разбор недооплаты, КСГ).")
    parser.add_argument("dbf", help="DBF-файл или папка с одним DBF")
    parser.add_argument("--day-kotd", default="10,15,12",
                        help="коды отделений дневного стационара (по умолчанию 10,15,12)")
    parser.add_argument("--kotd-names", default=None,
                        help="названия отделений: «23=Пульмонологическое; 27=Терапевтическое»")
    args = parser.parse_args()
    kotd_names = parse_kotd_names(args.kotd_names) if args.kotd_names else None
    try:
        res = run_economics(args.dbf, args.day_kotd, kotd_names=kotd_names)
    except JobError as e:
        print(f"ОШИБКА: {e}", file=sys.stderr)
        sys.exit(2)
    print(res["text"])
    print()
    print(f"Отчёт сохранён:      {res['txt_path']}")
    print(f"HTML для просмотра:  {res['html_path']}")


if __name__ == "__main__":
    main()
