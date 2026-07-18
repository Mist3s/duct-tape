"""Единая настройка логирования задач + обработчик для передачи строк в GUI."""

from __future__ import annotations

import logging
import queue
import sys
from pathlib import Path


class QueueLogHandler(logging.Handler):
    """Обработчик логов, складывающий отформатированные строки в очередь для GUI-потока."""

    def __init__(self, q: queue.Queue):
        super().__init__()
        self.q = q

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.q.put(("log", self.format(record)))
        except Exception:
            pass


def setup_job_logging(
    logger: logging.Logger,
    log_path: Path | None = None,
    extra_handlers: list[logging.Handler] | None = None,
    console: bool = True,
) -> None:
    """Настраивает логгер задачи: (опц.) файл + (опц.) консоль + доп. обработчики.

    Существующие обработчики снимаются, чтобы повторный запуск в одном процессе
    (типично для GUI) не задваивал вывод.
    """
    for h in list(logger.handlers):
        logger.removeHandler(h)
        try:
            h.close()
        except Exception:
            pass
    logger.setLevel(logging.INFO)
    logger.propagate = False
    fmt = logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s", "%H:%M:%S")
    handlers: list[logging.Handler] = []
    if console and sys.stdout is not None:
        handlers.append(logging.StreamHandler(sys.stdout))
    if log_path is not None:
        handlers.append(logging.FileHandler(log_path, encoding="utf-8"))
    handlers.extend(extra_handlers or [])
    for h in handlers:
        h.setFormatter(fmt)
        logger.addHandler(h)
