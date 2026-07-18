#!/usr/bin/env python3
"""Удаление ошибочных случаев из DBF-реестров по протоколам проверки (*.txt).

Что делает:
 1. Находит в папке текстовые протоколы (*.txt; кодировка cp1251/cp866/utf-8 —
    определяется автоматически).
 2. Из каждого протокола извлекает имя обработанного DBF («Обработан файл: X.dbf»)
    и все коды талонов из строк ошибок («код талона:NNNNNNNN»).
 3. Удаляет записи с этими кодами (поле KOD_TALON) из указанного в протоколе файла
    и из общего файла талонов (например 6_0090207t.dbf) — коды из всех протоколов.
 4. Перед изменением создаёт резервную копию семейства файлов в backup_<дата_время>,
    пишет атомарно и сбрасывает устаревшие индексы (см. omsreg.core.backup).
 5. Ведёт подробный лог (консоль + файл udalenie_talonov_<дата_время>.log).

Примеры запуска:
    omsreg-remove-talons re_gb3
    omsreg-remove-talons re_gb3 --dry-run
    omsreg-remove-talons re_gb3 --common 6_0090207t.dbf --field KOD_TALON

Повторный запуск безопасен: если совпадений больше нет, файлы не изменяются.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

from omsreg.core import TALON_FIELD_DEFAULT, DbfTable, JobError, as_code, setup_job_logging
from omsreg.core.backup import save_and_verify
from omsreg.core.textio import detect_and_read_text

RE_DBF_NAME = re.compile(r"Обработан\s+файл\s*:\s*([\w.\-]+\.dbf)", re.IGNORECASE)
RE_TALON = re.compile(r"код\s+талона\s*[:№]?\s*(\d+)", re.IGNORECASE)
RE_ERRCNT = re.compile(r"Количество\s+ошибок[^\d-]*-\s*(\d+)", re.IGNORECASE)
# запись об ошибке всегда начинается с 16-значного номера полиса
RE_POLIS_START = re.compile(r"^\d{16}\b")
# служебные строки, которые не могут быть продолжением перенесённой записи
SERVICE_STARTS = (
    "ВНИМАНИЕ", "ВЫЯВЛЕНО", "ФАЙЛ ", "ДУБЛИРУЕТСЯ", "СЛУЧАИ",
    "ПРОХОДИЛИ", "ОБРАБОТАН", "КОЛИЧЕСТВО",
)
# автоопределение общего файла талонов: 6_0090207t.dbf и т.п.
RE_COMMON_DBF = re.compile(r"^\d+_\d+t\.dbf$", re.IGNORECASE)

log = logging.getLogger("omsreg.utils.remove_error_talons")


# ----------------------------- работа с протоколами (*.txt) -----------------------------

def parse_protocol(path: Path) -> dict:
    """Разбирает протокол проверки. Возвращает словарь с dbf_name, codes (в порядке
    появления, с дублями), no_code_lines, wrapped, declared_errors, encoding."""
    text, enc = detect_and_read_text(path, log)
    m = RE_DBF_NAME.search(text)
    dbf_name = m.group(1) if m else None
    m_err = RE_ERRCNT.search(text)
    declared = int(m_err.group(1)) if m_err else None

    # Коды извлекаем по ВСЕМУ тексту: запись об ошибке бывает перенесена на несколько
    # строк, и «код талона:NNN» разорван переводом строки; \s+ захватывает и его.
    codes = RE_TALON.findall(text)

    # Логические записи: перенесённые физические строки склеиваем. Новая запись всегда
    # начинается с 16-значного полиса; строка без полиса и не служебная — продолжение.
    logical = []  # [номер первой физ. строки, текст, склеена_ли_из_переносов]
    cur = None
    for lineno, raw in enumerate(text.splitlines(), start=1):
        s = raw.strip().strip("\x1a\x00").strip()  # DOS-маркер конца файла и мусорные байты
        if not s or set(s) <= {"-", "=", "_"}:
            cur = None  # пустая строка/разделитель завершают запись
            continue
        if RE_POLIS_START.match(s):
            cur = [lineno, s, False]
            logical.append(cur)
        elif cur is not None and not s.upper().startswith(SERVICE_STARTS):
            cur[1] += " " + s  # продолжение перенесённой записи
            cur[2] = True
        else:
            logical.append([lineno, s, False])
            cur = None

    wrapped = [(ln, s) for ln, s, glued in logical if glued]

    no_code_lines = []  # содержательные записи без кода талона
    for lineno, s, _glued in logical:
        if RE_TALON.search(s):
            continue
        low = s.lower()
        if RE_DBF_NAME.search(s) or RE_ERRCNT.search(s) or "информация по лпу" in low:
            continue
        no_code_lines.append((lineno, s))

    # самоконтроль склейки: кодов в записях должно быть столько же, сколько в тексте
    codes_in_logical = sum(len(RE_TALON.findall(s)) for _ln, s, _g in logical)
    if codes_in_logical != len(codes):
        log.warning(
            "  ВНИМАНИЕ: %s — расхождение при склейке перенесённых строк "
            "(кодов по тексту: %d, по склеенным записям: %d); за основу взят полнотекстовый поиск",
            path.name, len(codes), codes_in_logical,
        )

    return {
        "dbf_name": dbf_name, "codes": codes, "no_code_lines": no_code_lines,
        "wrapped": wrapped, "declared_errors": declared, "encoding": enc,
    }


# ----------------------------- работа с DBF -----------------------------

def build_file_index(directory: Path) -> dict:
    """Индекс файлов папки без учёта регистра имени: 'd00902_07.dbf' -> Path."""
    return {p.name.lower(): p for p in directory.iterdir() if p.is_file()}


def find_common_dbf(directory: Path, explicit: str | None, index: dict) -> Path:
    if explicit:
        p = index.get(explicit.lower()) or (directory / explicit)
        if not p.exists():
            msg = f"Общий файл талонов не найден: {directory / explicit}"
            log.error(msg)
            raise JobError(msg)
        return p
    candidates = [p for name, p in index.items() if RE_COMMON_DBF.match(name)]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        msg = ("Не удалось автоматически найти общий файл талонов (вида 6_XXXXXXXt.dbf) в "
               f"{directory}. Укажите его в поле «Общий файл талонов» (или параметром --common).")
    else:
        msg = ("Найдено несколько кандидатов на общий файл талонов: "
               + ", ".join(p.name for p in candidates)
               + ". Укажите нужный в поле «Общий файл талонов» (или параметром --common).")
    log.error(msg)
    raise JobError(msg)


def process_dbf(dbf_path: Path, codes_with_sources: dict, field_name: str,
                backup_dir: Path, dry_run: bool) -> dict:
    """Удаляет из DBF записи, у которых код талона входит в codes_with_sources
    ({int(код): set(имена txt-источников)}). Возвращает статистику для сводки."""
    log.info("-" * 78)
    log.info("DBF: %s", dbf_path.name)
    table = DbfTable(dbf_path)
    log.info("  формат 0x%02X, кодировка данных %s, записей в заголовке: %d, длина записи: %d байт",
             table.version, table.codepage, table.nrec, table.record_len)
    if not table.trailing:
        log.info("  примечание: в исходном файле отсутствовал маркер конца файла (0x1A); "
                 "после сохранения он будет добавлен (это стандарт DBF)")

    fld = table.field(field_name)
    if fld is None:
        log.error("  ОШИБКА: в файле %s нет поля %s — файл пропущен", dbf_path.name, field_name)
        return {"path": dbf_path, "before": table.nrec, "deleted": 0, "after": table.nrec, "error": True}

    f_surname = table.field("SURNAME")
    f_name = table.field("NAME")

    kept, deleted = [], []
    per_code_hits = {code: 0 for code in codes_with_sources}
    already_marked_deleted = 0
    empty_code_records = 0

    for i, rec in enumerate(table.records, start=1):
        if table.is_deleted(rec):
            already_marked_deleted += 1
        code = table.code_value(rec, fld)
        if code is None:
            empty_code_records += 1
            kept.append(rec)
            continue
        if code in codes_with_sources:
            per_code_hits[code] += 1
            fio = ""
            if f_surname or f_name:
                parts = [table.value(rec, f) for f in (f_surname, f_name) if f]
                fio = " ".join(p for p in parts if p)
            src = ", ".join(sorted(codes_with_sources[code]))
            log.info("  УДАЛЯЕТСЯ запись №%d: %s=%s%s (источник: %s)",
                     i, fld.name, code, f", ФИО: {fio}" if fio else "", src)
            deleted.append(rec)
        else:
            kept.append(rec)

    found = {c: n for c, n in per_code_hits.items() if n > 0}
    not_found = sorted(c for c, n in per_code_hits.items() if n == 0)

    log.info("  Итог по файлу %s:", dbf_path.name)
    log.info("    записей было: %d", table.nrec)
    log.info("    подлежит удалению: %d (по %d уникальным кодам из %d искомых)",
             len(deleted), len(found), len(codes_with_sources))
    log.info("    останется: %d", len(kept))
    if already_marked_deleted:
        log.info("    записей с пометкой удаления (флаг '*'): %d", already_marked_deleted)
    if empty_code_records:
        log.info("    записей с пустым/нечисловым %s (пропущены при сравнении): %d",
                 fld.name, empty_code_records)
    multi = {c: n for c, n in found.items() if n > 1}
    for c, n in sorted(multi.items()):
        log.info("    код %s встретился в файле %d раза(з) — удалены все вхождения", c, n)
    if not_found:
        log.info("    коды, НЕ найденные в этом файле (%d): %s",
                 len(not_found), ", ".join(str(c) for c in not_found))
    _warn_large_deletion(dbf_path, len(deleted), table.nrec)

    result = {"path": dbf_path, "before": table.nrec, "deleted": len(deleted),
              "after": len(kept), "found": found, "not_found": not_found, "error": False}

    if not deleted:
        log.info("    изменений нет — файл не перезаписывается")
        return result
    if dry_run:
        log.info("    РЕЖИМ ПРОВЕРКИ (--dry-run): файл НЕ изменён")
        return result

    verify = save_and_verify(
        table, kept, dbf_path, field_name,
        lambda c: c is not None and c in codes_with_sources, backup_dir, log,
    )
    result["error"] = not verify["ok"]
    return result


def _warn_large_deletion(dbf_path: Path, deleted: int, total: int) -> None:
    """Предохранитель: заметное предупреждение, если удаляется большая доля файла —
    типичный признак неверного поля/файла кодов."""
    if total and (deleted == total or deleted / total > 0.30):
        share = deleted / total * 100
        log.warning("  ВНИМАНИЕ: из %s удаляется %d из %d записей (%.0f%%) — "
                    "убедитесь, что указаны правильные протоколы и поле кода талона!",
                    dbf_path.name, deleted, total, share)


# ----------------------------- основная логика -----------------------------

def run_removal(directory, common=None, field=TALON_FIELD_DEFAULT, dry_run=False,
                extra_handlers=None, console=True) -> dict:
    """Удаление ошибочных случаев по протоколам *.txt. Возвращает словарь с итогами.
    Фатальные ошибки поднимают JobError."""
    directory = Path(directory)
    if not directory.is_dir():
        raise JobError(f"Папка не найдена: {directory}")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = directory / f"udalenie_talonov_{ts}.log"
    setup_job_logging(log, log_path, extra_handlers, console)

    log.info("=" * 78)
    log.info("Запуск: папка %s%s", directory.resolve(), "  [РЕЖИМ ПРОВЕРКИ]" if dry_run else "")
    log.info("Лог-файл: %s", log_path)

    index = build_file_index(directory)
    common_dbf = find_common_dbf(directory, common, index)
    log.info("Общий файл талонов: %s", common_dbf.name)

    txt_files = sorted(p for p in directory.iterdir() if p.suffix.lower() == ".txt")
    if not txt_files:
        msg = f"В папке {directory} не найдено ни одного файла *.txt"
        log.error(msg)
        raise JobError(msg)

    # -------- шаг 1: сбор кодов из протоколов --------
    log.info("=" * 78)
    log.info("ШАГ 1. Чтение протоколов (*.txt): найдено %d файла(ов)", len(txt_files))

    plan = OrderedDict()
    all_codes_sources = {}

    def add_to_plan(dbf_path, code, source):
        plan.setdefault(dbf_path, {}).setdefault(code, set()).add(source)

    total_mentions = 0
    for txt in txt_files:
        info = parse_protocol(txt)
        codes_int, bad_codes = [], []
        for c in info["codes"]:
            nc = as_code(c)
            (codes_int if nc is not None else bad_codes).append(nc if nc is not None else c)
        unique = sorted(set(codes_int))
        dup = len(codes_int) - len(unique)
        total_mentions += len(codes_int)

        log.info("-" * 78)
        log.info("Протокол: %s (кодировка %s)", txt.name, info["encoding"])
        log.info("  указан обработанный файл: %s", info["dbf_name"] or "НЕ НАЙДЕН в тексте!")
        if info["declared_errors"] is not None:
            log.info("  заявлено ошибок в протоколе: %d", info["declared_errors"])
        log.info("  извлечено кодов талона: %d (уникальных: %d%s)",
                 len(codes_int), len(unique), f", повторов: {dup}" if dup else "")
        if info["wrapped"]:
            log.info("  обнаружены записи, перенесённые на несколько строк (%d шт.) — склеены:",
                     len(info["wrapped"]))
            for lineno, s in info["wrapped"]:
                log.info("    со стр.%d: %s", lineno, s[:160] + ("..." if len(s) > 160 else ""))
        if unique:
            log.info("  коды: %s", ", ".join(str(c) for c in unique))
        if bad_codes:
            log.warning("  нечисловые значения кода талона (пропущены): %s", bad_codes)
        if info["declared_errors"] is not None and info["declared_errors"] != len(codes_int):
            log.info("  примечание: заявлено ошибок %d, а строк с кодом талона %d — "
                     "часть ошибок в протоколе не содержит кода талона (это нормально)",
                     info["declared_errors"], len(codes_int))
        if info["no_code_lines"]:
            log.info("  строки протокола БЕЗ кода талона (%d шт.) — автоматически НЕ обрабатываются, "
                     "проверьте вручную:", len(info["no_code_lines"]))
            for lineno, s in info["no_code_lines"]:
                log.info("    стр.%4d: %s", lineno, s)

        if not unique:
            log.info("  кодов для удаления нет — протокол ничего не добавляет")
            continue

        if info["dbf_name"]:
            named = index.get(info["dbf_name"].lower())
            if named is None:
                log.warning("  ВНИМАНИЕ: файл %s, указанный в протоколе, отсутствует в папке — "
                            "его коды будут удалены только из общего файла %s",
                            info["dbf_name"], common_dbf.name)
            else:
                for c in unique:
                    add_to_plan(named, c, txt.name)
        else:
            log.warning("  ВНИМАНИЕ: имя DBF в протоколе не найдено — "
                        "коды будут удалены только из общего файла %s", common_dbf.name)

        for c in unique:
            add_to_plan(common_dbf, c, txt.name)
            all_codes_sources.setdefault(c, set()).add(txt.name)

    log.info("-" * 78)
    log.info("Всего по всем протоколам: упоминаний кодов %d, уникальных кодов %d",
             total_mentions, len(all_codes_sources))
    if not all_codes_sources:
        log.info("Удалять нечего. Завершение.")
        return {"had_error": False, "log_path": log_path, "deleted_total": 0,
                "files_changed": 0, "dry_run": dry_run}

    # -------- шаг 2: удаление из DBF --------
    log.info("=" * 78)
    log.info("ШАГ 2. Обработка DBF-файлов: %d файл(ов) в плане", len(plan))
    backup_dir = directory / f"backup_{ts}"
    results = []
    for dbf_path, codes in plan.items():
        try:
            results.append(process_dbf(dbf_path, codes, field, backup_dir, dry_run))
        except (ValueError, OSError) as e:
            log.error("  ОШИБКА обработки %s: %s (если файл был затронут — восстановите из %s)",
                      dbf_path.name, e, backup_dir)
            results.append({"path": dbf_path, "before": 0, "deleted": 0, "after": 0, "error": True})

    # -------- итоговая сводка --------
    log.info("=" * 78)
    log.info("ИТОГОВАЯ СВОДКА%s", " (режим проверки, файлы не изменялись)" if dry_run else "")
    w = max(len(r["path"].name) for r in results)
    log.info("  %-*s  %10s  %10s  %10s", w, "файл", "было", "удалено", "стало")
    for r in results:
        mark = "  <-- ОШИБКА, файл не изменён корректно" if r["error"] else ""
        log.info("  %-*s  %10d  %10d  %10d%s", w, r["path"].name, r["before"], r["deleted"], r["after"], mark)

    log.info("-" * 78)
    log.info("Сводка по кодам (найден/не найден в каждом файле из плана):")
    for code in sorted(all_codes_sources):
        parts = []
        for r in results:
            if r.get("found") is None:
                continue
            if code not in plan.get(r["path"], {}):
                continue
            nfound = r["found"].get(code, 0)
            parts.append(f"{r['path'].name}: {'удалено ' + str(nfound) if nfound else 'НЕ НАЙДЕН'}")
        log.info("  %s  [из %s]  ->  %s",
                 code, ", ".join(sorted(all_codes_sources[code])), "; ".join(parts))

    deleted_total = sum(r["deleted"] for r in results)
    files_changed = sum(1 for r in results if r["deleted"] and not r["error"])
    errors = [r for r in results if r["error"]]
    if errors:
        log.error("Завершено с ошибками в %d файле(ах) — см. лог выше!", len(errors))
    if not dry_run:
        log.info("Резервные копии изменённых файлов: %s",
                 backup_dir if backup_dir.exists() else "не потребовались")
    log.info("Готово. Полный лог: %s", log_path)
    return {"had_error": bool(errors), "log_path": log_path, "deleted_total": deleted_total,
            "files_changed": files_changed, "dry_run": dry_run}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Удаление ошибочных случаев из DBF-реестров по протоколам проверки (*.txt).")
    parser.add_argument("directory", help="папка с протоколами *.txt и DBF-файлами (например re_gb3)")
    parser.add_argument("--common", default=None,
                        help="имя общего файла талонов (по умолчанию ищется: 6_XXXXXXXt.dbf)")
    parser.add_argument("--field", default=TALON_FIELD_DEFAULT,
                        help=f"имя поля с кодом талона (по умолчанию {TALON_FIELD_DEFAULT})")
    parser.add_argument("--dry-run", action="store_true",
                        help="только показать, что будет удалено, ничего не изменяя")
    args = parser.parse_args()
    try:
        res = run_removal(Path(args.directory), args.common, args.field, args.dry_run)
    except JobError as e:
        if not log.handlers:
            print(f"ОШИБКА: {e}", file=sys.stderr)
        sys.exit(1)
    sys.exit(2 if res["had_error"] else 0)


if __name__ == "__main__":
    main()
