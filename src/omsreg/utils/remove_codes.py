#!/usr/bin/env python3
"""Удаление записей с заданными кодами талонов из ВСЕХ DBF-файлов папки.

Отличие от remove_error_talons:
  * коды берутся не из протоколов, а из простого текстового файла со списком кодов
    (по одному в строке либо через пробел/запятую/точку с запятой);
  * удаление идёт по всем *.dbf папки, где есть поле KOD_TALON (прочие пропускаются).

Из списка берутся числа длиной 6-12 цифр (--min-len/--max-len). Более короткие
(даты, телефоны) и длинные (16-значные полисы) пропускаются с предупреждением —
это защита от «не того файла». Бинарные файлы отклоняются. Перед изменением
создаётся резервная копия семейства файлов, запись атомарна (см. omsreg.core.backup).

Примеры запуска:
    omsreg-remove-codes re_gb3 коды.txt --dry-run
    omsreg-remove-codes re_gb3 коды.txt
    omsreg-remove-codes re_gb3 коды.txt --field KOD_TALON

Повторный запуск безопасен: если совпадений больше нет, файлы не изменяются.
"""

from __future__ import annotations

import argparse
import logging
import sys
from functools import partial
from pathlib import Path

from omsreg.core import TALON_FIELD_DEFAULT, DbfTable, JobError
from omsreg.core.backup import save_and_verify
from omsreg.core.cli import run_or_exit
from omsreg.core.textio import extract_code_tokens, read_codes_file
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

MIN_CODE_LEN = 6   # числа короче не считаем кодами (даты, телефоны, нумерация строк)
MAX_CODE_LEN = 12  # числа длиннее не считаем кодами (например, 16-значный полис)

log = logging.getLogger("omsreg.utils.remove_codes")


# ----------------------------- обработка одного DBF -----------------------------

def process_dbf(dbf_path: Path, codes: set, field_name: str,
                backup_dir: Path, dry_run: bool) -> dict:
    """Удаляет из DBF записи, у которых поле field_name входит в множество кодов."""
    log.info("-" * 78)
    log.info("DBF: %s", dbf_path.name)
    table = DbfTable(dbf_path)

    fld = table.field(field_name)
    if fld is None:
        log.info("  поля %s нет — файл пропущен (записей: %d)", field_name, table.nrec)
        return file_result(dbf_path, STATUS_NO_FIELD, before=table.nrec, deleted=0,
                           after=table.nrec)

    log.info("  формат 0x%02X, кодировка данных %s, записей: %d",
             table.version, table.codepage, table.nrec)

    f_surname = table.field("SURNAME")
    f_name = table.field("NAME")

    kept, deleted = [], []
    found: dict[int, int] = {}
    for i, rec in enumerate(table.records, start=1):
        code = table.code_value(rec, fld)
        if code is not None and code in codes:
            found[code] = found.get(code, 0) + 1
            fio = join_fio(table, rec, f_surname, f_name)
            log.info("  УДАЛЯЕТСЯ запись №%d: %s=%s%s",
                     i, fld.name, code, f", ФИО: {fio}" if fio else "")
            deleted.append(rec)
        else:
            kept.append(rec)

    log.info("  Итог по файлу %s: было %d, подлежит удалению %d (кодов найдено: %d), останется %d",
             dbf_path.name, table.nrec, len(deleted), len(found), len(kept))
    warn_large_deletion(dbf_path, len(deleted), table.nrec, log, "файл кодов и поле кода талона")

    result = file_result(dbf_path, STATUS_OK, before=table.nrec, deleted=len(deleted),
                         after=len(kept), found=found)

    if not deleted:
        log.info("    изменений нет — файл не перезаписывается")
        return result
    if dry_run:
        log.info("    РЕЖИМ ПРОВЕРКИ (--dry-run): файл НЕ изменён")
        return result

    verify = save_and_verify(
        table, kept, dbf_path, field_name, lambda c: c is not None and c in codes, backup_dir, log,
    )
    result["error"] = not verify["ok"]
    return result


# ----------------------------- основная логика -----------------------------

def resolve_codes_path(directory, codes_file) -> Path:
    """Находит файл со списком кодов: как указан, либо внутри папки directory."""
    directory = Path(directory)
    codes_path = Path(codes_file)
    if codes_path.is_file():
        return codes_path
    alt = directory / codes_file
    if alt.is_file():
        return alt
    raise JobError(f"Файл со списком кодов не найден: {codes_path}")


def _read_codes(codes_text, codes_path: Path | None, min_len: int, max_len: int) -> set:
    """ШАГ 1: читает список кодов из введённого текста (codes_path is None) или из файла.

    Пишет в журнал источник, состав списка и пропущенные числа. Пустой список кодов —
    ошибка задачи (JobError): удалять было бы нечего, а причина почти всегда в «не том
    файле».
    """
    log.info("=" * 78)
    log.info("ШАГ 1. Чтение списка кодов (принимаются числа длиной %d-%d цифр)", min_len, max_len)
    use_text = codes_path is None
    try:
        if use_text:
            codes_list, too_long, too_short = extract_code_tokens(codes_text, min_len, max_len)
            enc = "введён в программе"
        else:
            codes_list, enc, too_long, too_short = read_codes_file(codes_path, min_len, max_len)
    except ValueError as e:
        log.error("  %s", e)
        raise JobError(str(e)) from e
    codes = set(codes_list)
    dup = len(codes_list) - len(codes)
    log.info("  источник: %s", enc)
    log.info("  прочитано кодов: %d (уникальных: %d%s)",
             len(codes_list), len(codes), f", повторов: {dup}" if dup else "")
    if codes:
        log.info("  коды: %s", ", ".join(str(c) for c in sorted(codes)))
    if too_long:
        log.warning("  пропущены числа длиннее %d цифр (не похожи на код талона): %s",
                    max_len, ", ".join(too_long[:20]) + ("..." if len(too_long) > 20 else ""))
    if too_short:
        log.warning("  пропущены числа короче %d цифр (даты, телефоны, нумерация и т.п.): %s",
                    min_len, ", ".join(too_short[:20]) + ("..." if len(too_short) > 20 else ""))
    if not codes:
        msg = ("В введённом списке нет ни одного кода талона." if use_text
               else "В файле не найдено ни одного кода талона.")
        log.error("  %s", msg)
        raise JobError(msg)
    if len(too_long) + len(too_short) > len(codes_list):
        log.warning("  ВНИМАНИЕ: пропущенных чисел больше, чем принятых кодов — "
                    "убедитесь, что список кодов правильный!")
    return codes


def _find_dbf_files(directory: Path, codes_path: Path | None) -> list:
    """Все *.dbf папки по алфавиту, кроме самого файла со списком кодов."""
    return sorted(
        (p for p in directory.iterdir()
         if p.is_file() and p.suffix.lower() == ".dbf"
         and (codes_path is None or p.resolve() != codes_path.resolve())),
        key=lambda p: p.name.lower(),
    )


def _summary_note(field: str, r: dict) -> str:
    """Пометка строки итоговой таблицы: пропуск из-за отсутствия поля либо ошибка."""
    if r["status"] == STATUS_NO_FIELD:
        return f"  (нет поля {field} — пропущен)"
    return "  <-- ОШИБКА, файл не изменён корректно" if r["error"] else ""


def _log_code_report(results: list, codes: set, dry_run: bool) -> None:
    """Сводка по кодам: где каждый код удалён и какие коды не найдены ни в одном файле."""
    log.info("-" * 78)
    log.info("Сводка по кодам:")
    verb = "будет удалено" if dry_run else "удалено"
    not_found_anywhere = []
    for code in sorted(codes):
        parts = [f"{r['path'].name}: {r['found'][code]}"
                 for r in results if code in r["found"]]
        if parts:
            log.info("  %s  ->  %s: %s", code, verb, "; ".join(parts))
        else:
            not_found_anywhere.append(code)
    if not_found_anywhere:
        log.info("  коды, НЕ найденные НИ В ОДНОМ файле (%d): %s",
                 len(not_found_anywhere), ", ".join(str(c) for c in not_found_anywhere))


def run_codes(directory, codes_file=None, field=TALON_FIELD_DEFAULT, dry_run=False,
              min_len=MIN_CODE_LEN, max_len=MAX_CODE_LEN, codes_text=None,
              extra_handlers=None, console=True) -> dict:
    """Удаление записей со списком кодов из всех DBF папки.

    Возвращает словарь с итогами (см. removal_common.finish_removal). Источник кодов:
    непустой codes_text (вставленный/введённый список) имеет приоритет, иначе читается
    файл codes_file. Должно быть задано что-то одно.
    """
    directory = Path(directory)
    if not directory.is_dir():
        raise JobError(f"Папка не найдена: {directory}")

    use_text = bool(codes_text and codes_text.strip())
    if not use_text and not codes_file:
        raise JobError("Не указан ни файл со списком кодов, ни введённый список кодов.")
    codes_path = None if use_text else resolve_codes_path(directory, codes_file)

    ts, log_path = start_removal(log, directory, "udalenie_kodov", dry_run,
                                 extra_handlers, console)
    log.info("Источник кодов: %s", "введённый список (вставлен в программе)"
             if use_text else f"файл {codes_path.resolve()}")
    log.info("Лог-файл: %s", log_path)

    # -------- шаг 1: чтение списка кодов --------
    codes = _read_codes(codes_text, codes_path, min_len, max_len)

    # -------- шаг 2: проход по всем DBF --------
    dbf_files = _find_dbf_files(directory, codes_path)
    if not dbf_files:
        msg = f"В папке {directory} не найдено ни одного файла *.dbf"
        log.error(msg)
        raise JobError(msg)

    log.info("=" * 78)
    log.info("ШАГ 2. Обработка DBF-файлов: найдено %d файла(ов)", len(dbf_files))
    backup_dir = directory / f"backup_{ts}"
    results = process_files(
        ((p, codes) for p in dbf_files),
        partial(process_dbf, field_name=field, backup_dir=backup_dir, dry_run=dry_run),
        log, backup_dir,
    )

    # -------- итоговая сводка --------
    log.info("=" * 78)
    log.info("ИТОГОВАЯ СВОДКА%s", " (режим проверки, файлы не изменялись)" if dry_run else "")
    log_summary_table(results, log, partial(_summary_note, field))
    _log_code_report(results, codes, dry_run)

    if results and all(r["status"] == STATUS_NO_FIELD for r in results):
        msg = f"НИ В ОДНОМ DBF-файле нет поля {field} — проверьте имя поля. Ничего не удалено."
        log.error(msg)
        return job_summary(log_path, dry_run, had_error=True)

    return finish_removal(results, log, log_path, backup_dir, dry_run)


def main() -> None:  # noqa: D103 - назначение утилиты в ArgumentParser(description=...), попадает в --help
    parser = argparse.ArgumentParser(
        description="Удаление записей с заданными кодами талонов из ВСЕХ DBF-файлов папки.")
    parser.add_argument("directory", help="папка с DBF-файлами")
    parser.add_argument("codes_file", help="текстовый файл со списком кодов талонов")
    add_field_arg(parser)
    add_dry_run_arg(parser)
    parser.add_argument("--min-len", type=int, default=MIN_CODE_LEN,
                        help=f"минимальная длина кода в цифрах (по умолчанию {MIN_CODE_LEN})")
    parser.add_argument("--max-len", type=int, default=MAX_CODE_LEN,
                        help=f"максимальная длина кода в цифрах (по умолчанию {MAX_CODE_LEN})")
    args = parser.parse_args()
    res = run_or_exit(lambda: run_codes(Path(args.directory), args.codes_file, args.field,
                                        args.dry_run, args.min_len, args.max_len), log)
    sys.exit(1 if res["had_error"] else 0)


if __name__ == "__main__":
    main()
