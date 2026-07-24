#!/usr/bin/env python3
"""Статистика по DBF-файлу стационара (например uu/0091_016.dbf).

Что делает:
 1. Читает DBF (нужны поля KOTD, KMKB, STOIM, ISHOD; при наличии FACT считает койко-дни).
 2. Делит случаи на дневной стационар (KOTD из --day-kotd, по умолчанию 10,15,12) и
    круглосуточный (остальные отделения).
 3. Сохраняет три файла рядом с DBF:
      statistika_<имя>_<дата_время>.txt   — текстовый отчёт;
      statistika_<имя>_<дата_время>.csv   — таблица для Excel (';', utf-8-sig,
                                            десятичная запятая — русская локаль);
      statistika_<имя>_<дата_время>.html  — наглядный отчёт для браузера.
 4. Прогресс пишется в общий журнал (консоль/GUI) и в statistika_<...>.log.

Сбор данных и запуск — здесь; построение отчётов — в stat.stacionar_report.

Примеры запуска:
    omsreg-stat uu/0091_016.dbf
    omsreg-stat uu
    omsreg-stat uu/0091_016.dbf --day-kotd 10,15
"""

from __future__ import annotations

import argparse
import logging
from datetime import datetime

from omsreg.core import DbfTable, JobError, as_float, as_int, resolve_dbf_path, setup_job_logging
from omsreg.core.cli import run_or_exit
from omsreg.utils._shared.stat_common import (
    add_kotd_args,
    classify_type,
    normalize_fields,
    parse_day_kotd,
    resolve_kotd_names,
)
from omsreg.utils._shared.stat_stacionar_report import build_html, build_report

# имена полей DBF по умолчанию (можно переопределить в интерфейсе/через run_stat)
DEFAULT_FIELDS = {"kotd": "KOTD", "kmkb": "KMKB", "stoim": "STOIM",
                  "ishod": "ISHOD", "fact": "FACT"}

log = logging.getLogger("omsreg.utils.stat_stacionar")


def collect(table: DbfTable, day_kotd, fields=None):
    """Возвращает (список случаев, число исключённых удалённых, есть_ли_FACT).
    Случай: (тип_стационара, kotd, kmkb, ishod, stoim, fact)."""
    f = normalize_fields(DEFAULT_FIELDS, fields)
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
        st_type = classify_type(kotd, day_kotd)
        cases.append((st_type, kotd, kmkb, ishod, stoim, fact))
    return cases, deleted, has_fact


def run_stat(target, day_kotd="10,15,12", fields=None, kotd_names=None,
             extra_handlers=None, console=True) -> dict:
    """Строит статистику стационара и сохраняет .txt/.csv/.html рядом с DBF.
    kotd_names — словарь названий отделений {код: название}; None -> встроенный KOTD_NAMES.
    Прогресс идёт в общий журнал. Возвращает
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
    add_kotd_args(parser)
    args = parser.parse_args()
    res = run_or_exit(lambda: run_stat(args.dbf, args.day_kotd,
                                       kotd_names=resolve_kotd_names(args)), log)
    print(res["text"])
    print()
    print(f"Отчёт сохранён:      {res['txt_path']}")
    print(f"Таблица для Excel:   {res['csv_path']}")
    print(f"HTML для просмотра:  {res['html_path']}")


if __name__ == "__main__":
    main()
