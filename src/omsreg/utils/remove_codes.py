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
from datetime import datetime
from pathlib import Path

from omsreg.core import TALON_FIELD_DEFAULT, DbfTable, JobError, setup_job_logging
from omsreg.core.backup import save_and_verify
from omsreg.core.textio import extract_code_tokens, read_codes_file

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
        return {"path": dbf_path, "before": table.nrec, "deleted": 0, "after": table.nrec,
                "found": {}, "skipped": True, "error": False}

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
            fio = ""
            if f_surname or f_name:
                parts = [table.value(rec, f) for f in (f_surname, f_name) if f]
                fio = " ".join(p for p in parts if p)
            log.info("  УДАЛЯЕТСЯ запись №%d: %s=%s%s",
                     i, fld.name, code, f", ФИО: {fio}" if fio else "")
            deleted.append(rec)
        else:
            kept.append(rec)

    log.info("  Итог по файлу %s: было %d, подлежит удалению %d (кодов найдено: %d), останется %d",
             dbf_path.name, table.nrec, len(deleted), len(found), len(kept))
    _warn_large_deletion(dbf_path, len(deleted), table.nrec)

    result = {"path": dbf_path, "before": table.nrec, "deleted": len(deleted),
              "after": len(kept), "found": found, "skipped": False, "error": False}

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


def _warn_large_deletion(dbf_path: Path, deleted: int, total: int) -> None:
    """Предохранитель против неверного файла кодов/поля: заметное предупреждение при
    удалении большой доли записей."""
    if total and (deleted == total or deleted / total > 0.30):
        log.warning("  ВНИМАНИЕ: из %s удаляется %d из %d записей (%.0f%%) — "
                    "убедитесь, что указаны правильные файл кодов и поле кода талона!",
                    dbf_path.name, deleted, total, deleted / total * 100)


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


def run_codes(directory, codes_file=None, field=TALON_FIELD_DEFAULT, dry_run=False,
              min_len=MIN_CODE_LEN, max_len=MAX_CODE_LEN, codes_text=None,
              extra_handlers=None, console=True) -> dict:
    """Удаление записей со списком кодов из всех DBF папки. Возвращает словарь с итогами.
    Источник кодов: непустой codes_text (вставленный/введённый список) имеет приоритет,
    иначе читается файл codes_file. Должно быть задано что-то одно."""
    directory = Path(directory)
    if not directory.is_dir():
        raise JobError(f"Папка не найдена: {directory}")

    use_text = bool(codes_text and codes_text.strip())
    if not use_text and not codes_file:
        raise JobError("Не указан ни файл со списком кодов, ни введённый список кодов.")
    codes_path = None if use_text else resolve_codes_path(directory, codes_file)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = directory / f"udalenie_kodov_{ts}.log"
    setup_job_logging(log, log_path, extra_handlers, console)

    log.info("=" * 78)
    log.info("Запуск: папка %s%s", directory.resolve(), "  [РЕЖИМ ПРОВЕРКИ]" if dry_run else "")
    log.info("Источник кодов: %s", "введённый список (вставлен в программе)"
             if use_text else f"файл {codes_path.resolve()}")
    log.info("Лог-файл: %s", log_path)

    # -------- шаг 1: чтение списка кодов --------
    log.info("=" * 78)
    log.info("ШАГ 1. Чтение списка кодов (принимаются числа длиной %d-%d цифр)", min_len, max_len)
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

    # -------- шаг 2: проход по всем DBF --------
    dbf_files = sorted(
        (p for p in directory.iterdir()
         if p.is_file() and p.suffix.lower() == ".dbf"
         and (codes_path is None or p.resolve() != codes_path.resolve())),
        key=lambda p: p.name.lower(),
    )
    if not dbf_files:
        msg = f"В папке {directory} не найдено ни одного файла *.dbf"
        log.error(msg)
        raise JobError(msg)

    log.info("=" * 78)
    log.info("ШАГ 2. Обработка DBF-файлов: найдено %d файла(ов)", len(dbf_files))
    backup_dir = directory / f"backup_{ts}"
    results = []
    for dbf_path in dbf_files:
        try:
            results.append(process_dbf(dbf_path, codes, field, backup_dir, dry_run))
        except (ValueError, OSError) as e:
            log.error("  ОШИБКА обработки %s: %s (если файл был затронут — восстановите из %s)",
                      dbf_path.name, e, backup_dir)
            results.append({"path": dbf_path, "before": 0, "deleted": 0, "after": 0,
                            "found": {}, "skipped": False, "error": True})

    # -------- итоговая сводка --------
    log.info("=" * 78)
    log.info("ИТОГОВАЯ СВОДКА%s", " (режим проверки, файлы не изменялись)" if dry_run else "")
    w = max(len(r["path"].name) for r in results)
    log.info("  %-*s  %10s  %10s  %10s", w, "файл", "было", "удалено", "стало")
    for r in results:
        if r["skipped"]:
            note = f"  (нет поля {field} — пропущен)"
        elif r["error"]:
            note = "  <-- ОШИБКА, файл не изменён корректно"
        else:
            note = ""
        log.info("  %-*s  %10d  %10d  %10d%s",
                 w, r["path"].name, r["before"], r["deleted"], r["after"], note)

    log.info("-" * 78)
    log.info("Сводка по кодам:")
    verb = "будет удалено" if dry_run else "удалено"
    not_found_anywhere = []
    for code in sorted(codes):
        parts = [f"{r['path'].name}: {r['found'][code]}"
                 for r in results if code in r.get("found", {})]
        if parts:
            log.info("  %s  ->  %s: %s", code, verb, "; ".join(parts))
        else:
            not_found_anywhere.append(code)
    if not_found_anywhere:
        log.info("  коды, НЕ найденные НИ В ОДНОМ файле (%d): %s",
                 len(not_found_anywhere), ", ".join(str(c) for c in not_found_anywhere))

    deleted_total = sum(r["deleted"] for r in results)
    files_changed = sum(1 for r in results if r["deleted"] and not r["error"])

    if results and all(r["skipped"] for r in results):
        msg = f"НИ В ОДНОМ DBF-файле нет поля {field} — проверьте имя поля. Ничего не удалено."
        log.error(msg)
        return {"had_error": True, "log_path": log_path, "deleted_total": 0,
                "files_changed": 0, "dry_run": dry_run}

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
        description="Удаление записей с заданными кодами талонов из ВСЕХ DBF-файлов папки.")
    parser.add_argument("directory", help="папка с DBF-файлами")
    parser.add_argument("codes_file", help="текстовый файл со списком кодов талонов")
    parser.add_argument("--field", default=TALON_FIELD_DEFAULT,
                        help=f"имя поля с кодом талона (по умолчанию {TALON_FIELD_DEFAULT})")
    parser.add_argument("--dry-run", action="store_true",
                        help="только показать, что будет удалено, ничего не изменяя")
    parser.add_argument("--min-len", type=int, default=MIN_CODE_LEN,
                        help=f"минимальная длина кода в цифрах (по умолчанию {MIN_CODE_LEN})")
    parser.add_argument("--max-len", type=int, default=MAX_CODE_LEN,
                        help=f"максимальная длина кода в цифрах (по умолчанию {MAX_CODE_LEN})")
    args = parser.parse_args()
    try:
        res = run_codes(Path(args.directory), args.codes_file, args.field, args.dry_run,
                        args.min_len, args.max_len)
    except JobError as e:
        if not log.handlers:
            print(f"ОШИБКА: {e}", file=sys.stderr)
        sys.exit(1)
    sys.exit(2 if res["had_error"] else 0)


if __name__ == "__main__":
    main()
