"""Чтение и запись файла настроек (настройки.txt) в формате «ключ = значение».

Ключи выводятся из схемы параметров (``<id_утилиты>.<ключ>``), поэтому ручной
таблицы соответствий больше нет: приложение просто обходит реестр. Старые русские
ключи из прежней версии подхватываются через ParamSpec.legacy_key и при следующем
сохранении переписываются в новую схему — миграция прозрачна.
"""

from __future__ import annotations

import sys
from pathlib import Path

CONFIG_NAME = "настройки.txt"

HEADER_LINES = (
    "# Настройки программы «Обработка реестров ОМС».",
    "# Файл создаётся автоматически при первом запуске; можно править вручную.",
    "# Формат: ключ = значение. Сохраняется кнопкой «Сохранить настройки»",
    "# и автоматически при закрытии программы.",
    "",
)


def config_path() -> Path:
    """Путь к файлу настроек — рядом с программой (exe) или в текущей папке запуска."""
    # frozen -> папка рядом с exe, иначе текущая папка запуска
    base = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path.cwd()
    return base / CONFIG_NAME


def read_kv(path: Path) -> tuple[dict[str, str], list[str]]:
    """Читает файл «ключ = значение».

    Возвращает (значения, непонятые строки). Пустые строки и '#'-комментарии
    пропускаются молча, а строка без «=» — это опечатка в файле, который правят
    вручную, поэтому она попадает во второй элемент (с номером строки), а не
    исчезает бесследно.
    """
    data: dict[str, str] = {}
    problems: list[str] = []
    for num, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            problems.append(f"строка {num}: {line}")
            continue
        k, v = line.split("=", 1)
        data[k.strip()] = v.strip()
    return data, problems


def write_kv(path: Path, items: list[tuple[str, str]]) -> None:
    """Пишет заголовок-комментарий и строки «ключ = значение»."""
    lines = list(HEADER_LINES) + [f"{k} = {v}" for k, v in items]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
