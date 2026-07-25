"""Помощники, общие для обеих утилит удаления (codes, error_talons).

Сам цикл удаления у утилит свой (разная бухгалтерия: источники протоколов против
пропуска файлов без поля), а здесь — общая обвязка задачи (шапка журнала, безопасный
проход по файлам, итоговая таблица и хвост сводки), единая запись о судьбе файла,
предохранитель от массового удаления, сбор ФИО для лога и повторяющиеся аргументы
командной строки.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from omsreg.core import TALON_FIELD_DEFAULT, setup_job_logging

# доля удаляемых записей, выше которой показываем предупреждение (частый признак
# неверного файла кодов/поля)
LARGE_DELETION_SHARE = 0.30

# что стало с файлом (ключ "status" записи file_result)
STATUS_OK = "ok"              # файл просмотрен целиком, статистика достоверна
STATUS_NO_FIELD = "no_field"  # нет поля с кодом талона — файл не обрабатывался
STATUS_FAILED = "failed"      # сбой обработки: что стало с файлом, неизвестно

# то, что печатается в сводке вместо цифр, если статистика недостоверна
UNKNOWN_COUNT = "неизвестно"


def file_result(dbf_path: Path, status: str, before=None, deleted=None, after=None,
                found=None, error: bool = False) -> dict:
    """Единая запись о судьбе одного DBF-файла — из неё строятся все сводки.

    Обе утилиты удаления возвращают ровно этот набор ключей во всех ветках (включая
    сбойную), чтобы сводки не угадывали схему по наличию ключей:

    status  — одно из STATUS_*;
    before/deleted/after — записей было/удалено/осталось; None означает «неизвестно»;
    found   — {код: сколько раз встретился в этом файле};
    error   — файл не изменён корректно (сбой обработки либо неудачная самопроверка).
    """
    return {"path": dbf_path, "status": status, "before": before, "deleted": deleted,
            "after": after, "found": found or {}, "error": error}


def failed_result(dbf_path: Path) -> dict:
    """Запись о файле, обработка которого сорвалась: статистика неизвестна.

    Нули здесь были бы ложью — файл мог быть изменён частично, поэтому числа
    остаются None, и итоговая таблица печатает «неизвестно».
    """
    return file_result(dbf_path, STATUS_FAILED, error=True)


def start_removal(log: logging.Logger, directory: Path, log_name_prefix: str, dry_run: bool,
                  extra_handlers=None, console: bool = True) -> tuple[str, Path]:
    """Настраивает журнал задачи и печатает шапку. Возвращает (метка времени, путь к логу).

    Метка времени одна на задачу: по ней называются и лог-файл, и папка резервных
    копий. Остальные строки шапки (источник кодов, общий файл талонов, «Лог-файл»)
    печатает сама утилита — у них разный порядок.
    """
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = directory / f"{log_name_prefix}_{ts}.log"
    setup_job_logging(log, log_path, extra_handlers, console)
    log.info("=" * 78)
    log.info("Запуск: папка %s%s", directory.resolve(), "  [РЕЖИМ ПРОВЕРКИ]" if dry_run else "")
    return ts, log_path


def process_files(items, handler, log: logging.Logger, backup_dir: Path) -> list:
    """Обрабатывает файлы плана: handler(путь, коды) на каждую пару items.

    Сбой на одном файле не прерывает остальные: в журнал уходит сообщение вместе с
    трассировкой (log.exception), а в результаты — запись failed_result с неизвестной
    статистикой (подделывать нули нельзя: файл мог быть изменён частично).
    """
    results = []
    for dbf_path, codes in items:
        try:
            results.append(handler(dbf_path, codes))
        except (ValueError, OSError) as e:
            log.exception("  ОШИБКА обработки %s: %s (если файл был затронут — восстановите из %s)",
                          dbf_path.name, e, backup_dir)
            results.append(failed_result(dbf_path))
    return results


def job_summary(log_path: Path, dry_run: bool, had_error: bool,
                deleted_total: int = 0, files_changed: int = 0) -> dict:
    """Итоги задачи удаления — единственная схема того, что утилиты отдают наружу.

    На эти ключи смотрят и CLI (код возврата), и плагины GUI. Значения по умолчанию —
    для задач, в которых не обработан ни один файл (нечего удалять, нет нужного поля).
    """
    return {"had_error": had_error, "log_path": log_path, "deleted_total": deleted_total,
            "files_changed": files_changed, "dry_run": dry_run}


def finish_removal(results: list, log: logging.Logger, log_path: Path, backup_dir: Path,
                   dry_run: bool) -> dict:
    """Печатает хвост журнала (ошибки, резервные копии, «Готово») и собирает итоги задачи.

    Возвращает то, что обе утилиты отдают наружу: had_error, log_path, deleted_total,
    files_changed, dry_run. Файлы с неизвестной статистикой в deleted_total не
    попадают и изменёнными не считаются.
    """
    deleted_total = sum(r["deleted"] or 0 for r in results)
    files_changed = sum(1 for r in results if r["deleted"] and not r["error"])
    errors = [r for r in results if r["error"]]
    if errors:
        log.error("Завершено с ошибками в %d файле(ах) — см. лог выше!", len(errors))
    if not dry_run:
        log.info("Резервные копии изменённых файлов: %s",
                 backup_dir if backup_dir.exists() else "не потребовались")
    log.info("Готово. Полный лог: %s", log_path)
    return job_summary(log_path, dry_run, bool(errors), deleted_total, files_changed)


def warn_large_deletion(dbf_path: Path, deleted: int, total: int, log: logging.Logger,
                        source_hint: str) -> None:
    """Предупреждает, если из файла удаляется слишком большая доля записей.

    Порог — весь объём файла либо больше LARGE_DELETION_SHARE. source_hint уточняет,
    что перепроверить («протоколы и поле кода талона» / «файл кодов и поле кода талона»).
    """
    if total and (deleted == total or deleted / total > LARGE_DELETION_SHARE):
        log.warning("  ВНИМАНИЕ: из %s удаляется %d из %d записей (%.0f%%) — "
                    "убедитесь, что указаны правильные %s!",
                    dbf_path.name, deleted, total, deleted / total * 100, source_hint)


def join_fio(table, rec, f_surname, f_name) -> str:
    """ФИО одной записи из полей SURNAME/NAME (дескрипторы разрешены заранее).

    Пусто, если оба поля отсутствуют. Используется только для показа удаляемых
    записей в логе.
    """
    if not (f_surname or f_name):
        return ""
    parts = [table.value(rec, f) for f in (f_surname, f_name) if f]
    return " ".join(p for p in parts if p)


def _count(value) -> str:
    """Число для итоговой таблицы: «неизвестно» вместо None (статистика недостоверна)."""
    return UNKNOWN_COUNT if value is None else str(value)


def log_summary_table(results: list, log: logging.Logger, note=None) -> None:
    """Пишет в лог таблицу «файл / было / удалено / стало».

    Недостоверная статистика (None после сбоя обработки) печатается словом
    «неизвестно», а не нулями. note(r) -> str — необязательная пометка в конце
    строки (по умолчанию — отметка ошибки).
    """
    w = max(len(r["path"].name) for r in results)
    log.info("  %-*s  %10s  %10s  %10s", w, "файл", "было", "удалено", "стало")
    for r in results:
        mark = note(r) if note else ("  <-- ОШИБКА, файл не изменён корректно" if r["error"] else "")
        log.info("  %-*s  %10s  %10s  %10s%s", w, r["path"].name,
                 _count(r["before"]), _count(r["deleted"]), _count(r["after"]), mark)


# --- повторяющиеся аргументы командной строки ---

def add_field_arg(parser) -> None:
    """Добавляет в парсер аргумент --field (имя поля с кодом талона)."""
    parser.add_argument("--field", default=TALON_FIELD_DEFAULT,
                        help=f"имя поля с кодом талона (по умолчанию {TALON_FIELD_DEFAULT})")


def add_dry_run_arg(parser) -> None:
    """Добавляет в парсер аргумент --dry-run (режим проверки без изменений)."""
    parser.add_argument("--dry-run", action="store_true",
                        help="только показать, что будет удалено, ничего не изменяя")
