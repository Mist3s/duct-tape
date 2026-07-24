#!/usr/bin/env python3
"""Экономика и эффективность стационара по DBF-реестру ОМС (оплата по КСГ).

Управленческий отчёт «как отработали и где резерв»: доходность койки (₽ на
койко-день), честная разбивка недополученной выручки по причинам (что реально
вернуть длительностью лечения, а что нет), расшифровка групп КСГ диагнозами из
самого файла. Все числа и коэффициенты берутся динамически из файла.

Оплата идёт ЗА СЛУЧАЙ по группе КСГ (не за день) и равна:
    базовая ставка × вес группы КСГ × поправочный коэффициент × коэффициент оплаты.
Коэффициент оплаты: 1.0 — случай оплачен полностью, меньше 1 — оплата снижена
(короткий случай, перевод, смерть/самовольный уход, длительность ниже норматива
группы). «Недополучено» = полная сумма минус фактическая.

В полях DBF это: STOIM (стоимость случая), KOEF_Z (вес КСГ), KOEF_UP (поправочный
коэффициент), KOEF_PR (коэффициент оплаты), GRUPPA (код КСГ), FACT (койко-дни),
ISHOD (исход). Поправочный коэффициент задаётся по случаю; его точный смысл — в
правилах КСГ и тарифном соглашении, из выгрузки не определяется (это НЕ «уровень
отделения»: в одном отделении встречаются разные значения).

Модель оплаты и агрегации — здесь; построение отчётов — в stat.economics_report.

Примеры запуска:
    omsreg-econ data/0091_016.dbf
    omsreg-econ data/0091_016.dbf --day-kotd 10,15,12
"""

from __future__ import annotations

import argparse
import logging
from collections import Counter, defaultdict
from datetime import datetime
from statistics import median

from omsreg.core import DbfTable, JobError, as_float, as_int, resolve_dbf_path, setup_job_logging
from omsreg.core.cli import run_or_exit
from omsreg.utils._shared.stat_common import (
    DAY_TYPE,
    ISHOD_NAMES,
    ROUND_TYPE,
    add_kotd_args,
    classify_type,
    normalize_fields,
    parse_day_kotd,
    resolve_kotd_names,
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
    """Базовая ставка = стоимость / (вес × поправочный коэф. × коэф. оплаты). None, если данных нет."""
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


# Базовую ставку (БС) подписываем КОДОМ КСГ (st…/ds…), а НЕ «круглосуточный/дневной»:
# по данным ставку определяет префикс кода группы, а тип стационара в отчёте считается по
# отделению (KOTD) — эти классификации расходятся, поэтому слова здесь были бы неверны.
PREFIX_LABEL = {"st": "st… (круглосуточные КСГ)", "ds": "ds… (дневные КСГ)"}


def base_rates_by_type(cases):
    """Медианная базовая ставка (БС) по префиксу кода КСГ: {'st': …, 'ds': …}."""
    by = defaultdict(list)
    for c in cases:
        b = base_rate(c["stoim"], c["kz"], c["kup"], c["kpr"])
        if b:
            by[ksg_prefix(c["gruppa"])].append(b)
    return {p: median(v) for p, v in by.items() if v}


def koef_counts(cases, key, reverse=False):
    """[(значение, число случаев)] для коэффициента key ('kup'/'kpr'), по возрастанию значения."""
    c = Counter(x[key] for x in cases if x[key] is not None)
    return sorted(c.items(), reverse=reverse)


# ----------------------------- сбор данных -----------------------------

def collect(table: DbfTable, day_kotd, fields=None):
    """Собирает случаи со стоимостными полями. Возвращает (список dict-ов, deleted, доступные поля)."""
    f = normalize_fields(ECON_FIELDS, fields)
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
            "type": classify_type(kotd, day_kotd),
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
        # вес КСГ (KOEF_Z) и поправочный коэф. (KOEF_UP) — в норме по одному на группу;
        # тариф полного случая = БС × вес × поправочный коэф.
        kzs = sorted({round(c["kz"], 4) for c in cs if c["kz"] is not None})
        kups = sorted({round(c["kup"], 4) for c in cs if c["kup"] is not None})
        fts = [full_payment(c["stoim"], c["kpr"]) for c in cs if c["stoim"] and c["kpr"]]
        rows.append({
            "g": g, "type": Counter(c["type"] for c in cs).most_common(1)[0][0],
            "n": len(cs), "sum": s,
            "per_day": _per_day(s, cs),
            "kz": kzs[0] if len(kzs) == 1 else None,   # None, если у группы разные веса (диапазон)
            "kz_range": (kzs[0], kzs[-1]) if kzs else None,
            "kup": kups[0] if len(kups) == 1 else None,
            "kup_range": (kups[0], kups[-1]) if kups else None,
            "full_tariff": median(fts) if fts else None,  # оплата за полностью пролеченный случай
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


def coef_str(value, rng) -> str:
    """Коэффициент строкой: одно значение, диапазон (если в группе разные) или «—»."""
    if value is not None:
        return f"{value:g}"
    if rng:
        return f"{rng[0]:g}–{rng[1]:g}"
    return "—"


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


# ----------------------------- запуск -----------------------------

def run_economics(target, day_kotd="10,15,12", fields=None, kotd_names=None,
                  extra_handlers=None, console=True) -> dict:
    """Строит экономический отчёт стационара (.txt/.html рядом с DBF). -> dict с путями и итогами."""
    from omsreg.utils._shared.stat_economics_report import build_html, build_report

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
    add_kotd_args(parser)
    args = parser.parse_args()
    res = run_or_exit(lambda: run_economics(args.dbf, args.day_kotd,
                                            kotd_names=resolve_kotd_names(args)), log)
    print(res["text"])
    print()
    print(f"Отчёт сохранён:      {res['txt_path']}")
    print(f"HTML для просмотра:  {res['html_path']}")


if __name__ == "__main__":
    main()
