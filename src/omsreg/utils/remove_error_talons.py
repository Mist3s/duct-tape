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
from functools import partial
from pathlib import Path
from typing import NamedTuple

from omsreg.core import TALON_FIELD_DEFAULT, DbfTable, JobError, as_code
from omsreg.core.backup import save_and_verify
from omsreg.core.cli import run_or_exit
from omsreg.core.textio import detect_and_read_text
from omsreg.utils._shared.removal_common import (
    STATUS_NO_FIELD,
    STATUS_OK,
    add_dry_run_arg,
    add_field_arg,
    file_result,
    finish_removal,
    job_summary,
    join_fio,
    log_summary_table,
    process_files,
    start_removal,
    warn_large_deletion,
)

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
    """Разбирает протокол проверки.

    Возвращает словарь с ключами dbf_name, codes (в порядке появления, с дублями),
    no_code_lines, wrapped, declared_errors, encoding.
    """
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
    """Общий файл талонов: указанный явно либо единственный подходящий (6_XXXXXXXt.dbf)."""
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


class _Scan(NamedTuple):
    """Результат прохода по записям одного DBF: что остаётся, что удаляется и почему."""

    kept: list           # записи, которые остаются в файле
    deleted: list        # записи под удаление (код талона найден в плане)
    hits: dict           # {код: сколько раз встретился} по ВСЕМ искомым кодам, включая нули
    marked_deleted: int  # записей с флагом удаления '*' в исходном файле
    empty_code: int      # записей с пустым/нечисловым кодом талона

    def found(self) -> dict:
        """Только реально встретившиеся коды: {код: сколько раз} (нули отброшены)."""
        return {c: n for c, n in self.hits.items() if n > 0}


def _scan_records(table, fld, codes_with_sources: dict) -> _Scan:
    """Делит записи файла на остающиеся и удаляемые, попутно считая статистику.

    Каждая удаляемая запись пишется в журнал с ФИО и именем протокола-источника.
    """
    f_surname = table.field("SURNAME")
    f_name = table.field("NAME")

    kept, deleted = [], []
    per_code_hits = dict.fromkeys(codes_with_sources, 0)
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
            fio = join_fio(table, rec, f_surname, f_name)
            src = ", ".join(sorted(codes_with_sources[code]))
            log.info("  УДАЛЯЕТСЯ запись №%d: %s=%s%s (источник: %s)",
                     i, fld.name, code, f", ФИО: {fio}" if fio else "", src)
            deleted.append(rec)
        else:
            kept.append(rec)

    return _Scan(kept, deleted, per_code_hits, already_marked_deleted, empty_code_records)


def _log_scan(dbf_path: Path, table, fld, scan: _Scan, codes_with_sources: dict) -> None:
    """Пишет в журнал итог по файлу: сколько было, уйдёт и останется, и что настораживает."""
    found = scan.found()
    not_found = sorted(c for c, n in scan.hits.items() if n == 0)

    log.info("  Итог по файлу %s:", dbf_path.name)
    log.info("    записей было: %d", table.nrec)
    log.info("    подлежит удалению: %d (по %d уникальным кодам из %d искомых)",
             len(scan.deleted), len(found), len(codes_with_sources))
    log.info("    останется: %d", len(scan.kept))
    if scan.marked_deleted:
        log.info("    записей с пометкой удаления (флаг '*'): %d", scan.marked_deleted)
    if scan.empty_code:
        log.info("    записей с пустым/нечисловым %s (пропущены при сравнении): %d",
                 fld.name, scan.empty_code)
    for c, n in sorted(found.items()):
        if n > 1:
            log.info("    код %s встретился в файле %d раза(з) — удалены все вхождения", c, n)
    if not_found:
        log.info("    коды, НЕ найденные в этом файле (%d): %s",
                 len(not_found), ", ".join(str(c) for c in not_found))
    warn_large_deletion(dbf_path, len(scan.deleted), table.nrec, log,
                        "протоколы и поле кода талона")


def process_dbf(dbf_path: Path, codes_with_sources: dict, field_name: str,
                backup_dir: Path, dry_run: bool) -> dict:
    """Удаляет из DBF записи, у которых код талона входит в codes_with_sources.

    codes_with_sources — {int(код): set(имена txt-источников)}. Возвращает запись о
    судьбе файла для сводок (см. removal_common.file_result).
    """
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
        return file_result(dbf_path, STATUS_NO_FIELD, before=table.nrec, deleted=0,
                           after=table.nrec, error=True)

    scan = _scan_records(table, fld, codes_with_sources)
    _log_scan(dbf_path, table, fld, scan, codes_with_sources)

    kept, deleted = scan.kept, scan.deleted
    result = file_result(dbf_path, STATUS_OK, before=table.nrec, deleted=len(deleted),
                         after=len(kept), found=scan.found())

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


# ----------------------------- план удаления по протоколам -----------------------------

def _split_codes(raw_codes: list) -> tuple[list, list]:
    """Делит извлечённые из протокола коды на числовые и нечисловые (для предупреждения)."""
    codes_int, bad_codes = [], []
    for c in raw_codes:
        nc = as_code(c)
        if nc is None:
            bad_codes.append(c)
        else:
            codes_int.append(nc)
    return codes_int, bad_codes


def _add_to_plan(plan: dict, dbf_path: Path, code: int, source: str) -> None:
    """Дописывает в план: из dbf_path удалить code, требование пришло из протокола source."""
    plan.setdefault(dbf_path, {}).setdefault(code, set()).add(source)


def _log_protocol(txt: Path, info: dict, codes_int: list, unique: list, bad_codes: list) -> None:
    """Пишет в журнал разбор одного протокола: коды, склейки, строки без кода талона."""
    dup = len(codes_int) - len(unique)
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


def _named_target(dbf_name: str | None, index: dict, common_dbf: Path) -> Path | None:
    """Файл, указанный в протоколе («Обработан файл: X.dbf»), если он есть в папке.

    Возвращает None, если имя в тексте не найдено или такого файла в папке нет: тогда
    коды протокола уйдут только из общего файла талонов, о чём предупреждает журнал.
    """
    if not dbf_name:
        log.warning("  ВНИМАНИЕ: имя DBF в протоколе не найдено — "
                    "коды будут удалены только из общего файла %s", common_dbf.name)
        return None
    named = index.get(dbf_name.lower())
    if named is None:
        log.warning("  ВНИМАНИЕ: файл %s, указанный в протоколе, отсутствует в папке — "
                    "его коды будут удалены только из общего файла %s",
                    dbf_name, common_dbf.name)
    return named


def _collect_plan(txt_files: list, index: dict, common_dbf: Path) -> tuple[dict, dict, int]:
    """ШАГ 1: строит план удаления по всем протоколам, попутно ведя журнал разбора.

    Возвращает (plan, all_codes_sources, total_mentions): plan — упорядоченный словарь
    {DBF: {код: множество имён протоколов}}, all_codes_sources — источники по каждому
    уникальному коду, total_mentions — сколько всего упоминаний кодов встретилось.
    """
    plan = OrderedDict()
    all_codes_sources: dict = {}
    total_mentions = 0
    for txt in txt_files:
        info = parse_protocol(txt)
        codes_int, bad_codes = _split_codes(info["codes"])
        unique = sorted(set(codes_int))
        total_mentions += len(codes_int)

        _log_protocol(txt, info, codes_int, unique, bad_codes)
        if not unique:
            log.info("  кодов для удаления нет — протокол ничего не добавляет")
            continue

        named = _named_target(info["dbf_name"], index, common_dbf)
        for c in unique:
            if named is not None:
                _add_to_plan(plan, named, c, txt.name)  # файл, указанный в протоколе
            _add_to_plan(plan, common_dbf, c, txt.name)  # и общий файл талонов — всегда
            all_codes_sources.setdefault(c, set()).add(txt.name)
    return plan, all_codes_sources, total_mentions


def _log_code_report(results: list, plan: dict, all_codes_sources: dict) -> None:
    """Сводка по кодам: найден или не найден каждый код в каждом файле своего плана.

    Файлы, которые не были просмотрены (нет поля, сбой обработки), в сводку не
    попадают: про их содержимое ничего не известно.
    """
    log.info("-" * 78)
    log.info("Сводка по кодам (найден/не найден в каждом файле из плана):")
    for code in sorted(all_codes_sources):
        parts = []
        for r in results:
            if r["status"] != STATUS_OK or code not in plan.get(r["path"], {}):
                continue
            nfound = r["found"].get(code, 0)
            parts.append(f"{r['path'].name}: {'удалено ' + str(nfound) if nfound else 'НЕ НАЙДЕН'}")
        log.info("  %s  [из %s]  ->  %s",
                 code, ", ".join(sorted(all_codes_sources[code])), "; ".join(parts))


# ----------------------------- основная логика -----------------------------

def run_removal(directory, common=None, field=TALON_FIELD_DEFAULT, dry_run=False,
                extra_handlers=None, console=True) -> dict:
    """Удаление ошибочных случаев по протоколам *.txt.

    Возвращает словарь с итогами (см. removal_common.finish_removal). Фатальные ошибки
    поднимают JobError.
    """
    directory = Path(directory)
    if not directory.is_dir():
        raise JobError(f"Папка не найдена: {directory}")

    ts, log_path = start_removal(log, directory, "udalenie_talonov", dry_run,
                                 extra_handlers, console)
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
    plan, all_codes_sources, total_mentions = _collect_plan(txt_files, index, common_dbf)

    log.info("-" * 78)
    log.info("Всего по всем протоколам: упоминаний кодов %d, уникальных кодов %d",
             total_mentions, len(all_codes_sources))
    if not all_codes_sources:
        log.info("Удалять нечего. Завершение.")
        return job_summary(log_path, dry_run, had_error=False)

    # -------- шаг 2: удаление из DBF --------
    log.info("=" * 78)
    log.info("ШАГ 2. Обработка DBF-файлов: %d файл(ов) в плане", len(plan))
    backup_dir = directory / f"backup_{ts}"
    results = process_files(
        plan.items(),
        partial(process_dbf, field_name=field, backup_dir=backup_dir, dry_run=dry_run),
        log, backup_dir,
    )

    # -------- итоговая сводка --------
    log.info("=" * 78)
    log.info("ИТОГОВАЯ СВОДКА%s", " (режим проверки, файлы не изменялись)" if dry_run else "")
    log_summary_table(results, log)
    _log_code_report(results, plan, all_codes_sources)

    return finish_removal(results, log, log_path, backup_dir, dry_run)


def main() -> None:  # noqa: D103 - назначение утилиты в ArgumentParser(description=...), попадает в --help
    parser = argparse.ArgumentParser(
        description="Удаление ошибочных случаев из DBF-реестров по протоколам проверки (*.txt).")
    parser.add_argument("directory", help="папка с протоколами *.txt и DBF-файлами (например re_gb3)")
    parser.add_argument("--common", default=None,
                        help="имя общего файла талонов (по умолчанию ищется: 6_XXXXXXXt.dbf)")
    add_field_arg(parser)
    add_dry_run_arg(parser)
    args = parser.parse_args()
    res = run_or_exit(lambda: run_removal(Path(args.directory), args.common, args.field, args.dry_run), log)
    sys.exit(1 if res["had_error"] else 0)


if __name__ == "__main__":
    main()
