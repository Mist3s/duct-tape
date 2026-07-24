"""Помощники, общие для обеих утилит удаления (codes, error_talons).

Сам цикл удаления у утилит свой (разная бухгалтерия: источники протоколов против
пропуска файлов без поля), а здесь — предохранитель от массового удаления, сбор
ФИО для лога, итоговая таблица и повторяющиеся аргументы командной строки.
"""

from __future__ import annotations

import logging
from pathlib import Path

from omsreg.core import TALON_FIELD_DEFAULT

# доля удаляемых записей, выше которой показываем предупреждение (частый признак
# неверного файла кодов/поля)
LARGE_DELETION_SHARE = 0.30


def warn_large_deletion(dbf_path: Path, deleted: int, total: int, log: logging.Logger,
                        source_hint: str) -> None:
    """Предупреждает, если из файла удаляется весь объём или больше LARGE_DELETION_SHARE
    записей. source_hint уточняет, что перепроверить («протоколы и поле кода талона» /
    «файл кодов и поле кода талона»)."""
    if total and (deleted == total or deleted / total > LARGE_DELETION_SHARE):
        log.warning("  ВНИМАНИЕ: из %s удаляется %d из %d записей (%.0f%%) — "
                    "убедитесь, что указаны правильные %s!",
                    dbf_path.name, deleted, total, deleted / total * 100, source_hint)


def join_fio(table, rec, f_surname, f_name) -> str:
    """ФИО из полей SURNAME/NAME (дескрипторы разрешены заранее) одной записи; пусто,
    если оба поля отсутствуют. Используется только для показа удаляемых записей в логе."""
    if not (f_surname or f_name):
        return ""
    parts = [table.value(rec, f) for f in (f_surname, f_name) if f]
    return " ".join(p for p in parts if p)


def log_summary_table(results: list, log: logging.Logger, note=None) -> None:
    """Пишет в лог таблицу «файл / было / удалено / стало». note(r) -> str —
    необязательная пометка в конце строки (по умолчанию — отметка ошибки)."""
    w = max(len(r["path"].name) for r in results)
    log.info("  %-*s  %10s  %10s  %10s", w, "файл", "было", "удалено", "стало")
    for r in results:
        mark = note(r) if note else ("  <-- ОШИБКА, файл не изменён корректно" if r["error"] else "")
        log.info("  %-*s  %10d  %10d  %10d%s",
                 w, r["path"].name, r["before"], r["deleted"], r["after"], mark)


# --- повторяющиеся аргументы командной строки ---

def add_field_arg(parser) -> None:
    parser.add_argument("--field", default=TALON_FIELD_DEFAULT,
                        help=f"имя поля с кодом талона (по умолчанию {TALON_FIELD_DEFAULT})")


def add_dry_run_arg(parser) -> None:
    parser.add_argument("--dry-run", action="store_true",
                        help="только показать, что будет удалено, ничего не изменяя")
