"""Резервное копирование семейства файлов DBF и безопасная упаковка с самопроверкой.

Проблема, которую это решает (см. ревью, раздел data-safety): DBF почти всегда
сопровождается файлами-спутниками одного имени — мемо .fpt/.dbt и индексы
.cdx/.idx/.ndx/.mdx. При физическом удалении записей номера записей сдвигаются,
и старый индекс начинает указывать не на те строки, а потребитель (FoxPro) молча
открывает его по флагу в заголовке. Поэтому здесь:

  * в резервную копию попадает ВСЁ семейство (а не только .dbf), чтобы восстановление
    было целостным;
  * после упаковки устаревшие индексы удаляются, а флаг структурного индекса в
    заголовке сбрасывается (см. DbfTable.save(clear_structural_index=True)) — софт
    перестроит индекс заново вместо чтения испорченного;
  * запись атомарна (DbfTable.save), а самопроверка перечитывает файл и убеждается,
    что искомых значений не осталось. При любой ошибке путь к бэкапу пишется в лог.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Callable

# спутники, которые копируем в бэкап рядом с .dbf
MEMO_SUFFIXES = (".fpt", ".dbt")
INDEX_SUFFIXES = (".cdx", ".idx", ".ndx", ".mdx")
COMPANION_SUFFIXES = MEMO_SUFFIXES + INDEX_SUFFIXES


def find_companions(dbf_path: Path) -> list[Path]:
    """Существующие файлы-спутники того же имени (регистр расширения не важен)."""
    dbf_path = Path(dbf_path)
    wanted = {s.lower() for s in COMPANION_SUFFIXES}
    found = []
    for p in dbf_path.parent.iterdir():
        if not p.is_file() or p == dbf_path:
            continue
        if p.stem.lower() == dbf_path.stem.lower() and p.suffix.lower() in wanted:
            found.append(p)
    return sorted(found, key=lambda p: p.name.lower())


def backup_table_family(dbf_path: Path, backup_dir: Path, log: logging.Logger | None = None) -> Path:
    """Копирует .dbf и все его спутники в backup_dir. Возвращает путь копии .dbf."""
    dbf_path = Path(dbf_path)
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    dbf_backup = backup_dir / dbf_path.name
    shutil.copy2(dbf_path, dbf_backup)
    if log:
        log.info("    резервная копия: %s", dbf_backup)
    for comp in find_companions(dbf_path):
        shutil.copy2(comp, backup_dir / comp.name)
        if log:
            log.info("    резервная копия спутника: %s", comp.name)
    return dbf_backup


def _drop_stale_indexes(dbf_path: Path, log: logging.Logger | None = None) -> list[str]:
    """Удаляет устаревшие индексы-спутники после упаковки. Возвращает имена удалённых."""
    dropped = []
    for comp in find_companions(dbf_path):
        if comp.suffix.lower() in INDEX_SUFFIXES:
            comp.unlink()
            dropped.append(comp.name)
    if dropped and log:
        log.info(
            "    удалены устаревшие индексы (%s) — потребитель перестроит их заново",
            ", ".join(dropped),
        )
    return dropped


def save_and_verify(
    table,
    kept: list[bytes],
    dbf_path: Path,
    field: str,
    code_matches: Callable[[int | None], bool],
    backup_dir: Path,
    log: logging.Logger,
) -> dict:
    """Полный безопасный цикл упаковки одного файла:

    бэкап семейства -> атомарная запись survivors со сбросом флага индекса ->
    удаление устаревших индексов -> перечитывание и проверка, что искомых значений
    не осталось. Возвращает {backup, nrec_after, remaining, ok}.

    field           — имя поля, по которому проверяем отсутствие удалённого;
    code_matches    — предикат code(int|None) -> bool «эта запись должна была уйти».
    """
    backup_path = backup_table_family(dbf_path, backup_dir, log)

    table.save(kept, dbf_path, clear_structural_index=True)
    _drop_stale_indexes(dbf_path, log)

    check = table.__class__(dbf_path)
    chk_fld = check.field(field)
    remaining = sum(1 for rec in check.records if code_matches(check.code_value(rec, chk_fld)))
    ok = check.nrec == len(kept) and remaining == 0
    if not ok:
        log.error(
            "    ОШИБКА САМОПРОВЕРКИ: записей %d (ожидалось %d), осталось искомых значений: %d. "
            "Исходный файл сохранён в %s — при необходимости восстановите из него.",
            check.nrec,
            len(kept),
            remaining,
            backup_path,
        )
    else:
        log.info("    файл сохранён и проверен: записей %d, искомых значений не осталось", check.nrec)
    return {"backup": backup_path, "nrec_after": check.nrec, "remaining": remaining, "ok": ok}
