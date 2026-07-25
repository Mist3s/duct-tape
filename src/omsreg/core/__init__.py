"""omsreg.core — общий код, не зависящий от графического интерфейса.

Примитивы, на которых строятся все утилиты: DbfTable/DbfField и resolve_dbf_path
(чтение и безопасная запись DBF), преобразователи и форматтеры значений, JobError,
настройка логирования задач, резервное копирование и разбор текста. Ни один модуль
пакета не импортирует tkinter — поэтому всё тестируется без дисплея.
"""

__all__ = [
    "DbfTable",
    "DbfField",
    "LDID_CODEPAGES",
    "TALON_FIELD_DEFAULT",
    "resolve_dbf_path",
    "as_code",
    "as_int",
    "as_float",
    "money",
    "csv_num",
    "pct",
    "JobError",
    "setup_job_logging",
    "QueueLogHandler",
]

from omsreg.core.convert import as_code, as_float, as_int
from omsreg.core.dbf import (
    LDID_CODEPAGES,
    TALON_FIELD_DEFAULT,
    DbfField,
    DbfTable,
    resolve_dbf_path,
)
from omsreg.core.errors import JobError
from omsreg.core.format import csv_num, money, pct
from omsreg.core.logging_setup import QueueLogHandler, setup_job_logging
