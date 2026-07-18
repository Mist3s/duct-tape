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
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
    else:
        base = Path.cwd()
    return base / CONFIG_NAME


def read_kv(path: Path) -> dict[str, str]:
    """Читает файл «ключ = значение» -> словарь. Пустые строки и '#'-комментарии пропускаются."""
    data: dict[str, str] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        data[k.strip()] = v.strip()
    return data


def write_kv(path: Path, items: list[tuple[str, str]]) -> None:
    """Пишет заголовок-комментарий и строки «ключ = значение»."""
    lines = list(HEADER_LINES) + [f"{k} = {v}" for k, v in items]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
