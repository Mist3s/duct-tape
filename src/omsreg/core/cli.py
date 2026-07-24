"""Обвязка командной строки: запуск задачи с единой обработкой фатальных ошибок."""

from __future__ import annotations

import logging
import sys

from omsreg.core.errors import JobError


def run_or_exit(run, log: logging.Logger):
    """Вызывает run() и возвращает его результат. При JobError печатает текст в
    stderr (только если журнал ещё не подхватил ошибку) и завершает процесс с
    кодом 2 — «некорректный ввод». Специфику успешного пути (коды/печать) оставляет
    вызывающему."""
    try:
        return run()
    except JobError as e:
        if not log.handlers:
            print(f"ОШИБКА: {e}", file=sys.stderr)
        sys.exit(2)
