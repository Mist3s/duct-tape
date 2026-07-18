"""omsreg.core — общий код, не зависящий от графического интерфейса.

Здесь живут примитивы, которые раньше были скопированы в каждый скрипт:
DbfTable/DbfField (чтение и безопасная запись DBF), преобразователи значений,
JobError, настройка логирования задач, резервное копирование и разбор текста.
Ни один модуль этого пакета не импортирует tkinter — поэтому всё тестируется без дисплея.
"""

from omsreg.core.convert import as_code, as_float, as_int
from omsreg.core.dbf import LDID_CODEPAGES, TALON_FIELD_DEFAULT, DbfField, DbfTable
from omsreg.core.errors import JobError
from omsreg.core.logging_setup import QueueLogHandler, setup_job_logging

__all__ = [
    "DbfTable",
    "DbfField",
    "LDID_CODEPAGES",
    "TALON_FIELD_DEFAULT",
    "as_code",
    "as_int",
    "as_float",
    "JobError",
    "setup_job_logging",
    "QueueLogHandler",
]
