"""Реестр утилит: сбор всех UtilitySpec из пакета omsreg.gui.plugins.

Порядок сбора:
  1. Явно перечисленные встроенные плагины (omsreg.gui.plugins.BUILTIN) — они
     статически импортированы, поэтому гарантированно есть и в собранном .exe.
  2. Досканирование папки plugins через pkgutil — подхватывает дополнительные
     модули, положенные рядом при запуске ИЗ ИСХОДНИКОВ. В замороженном
     (PyInstaller onefile) приложении этот скан обычно пуст, и это нормально —
     список наполняется из BUILTIN.

Чтобы добавить утилиту: положите модуль с объектом SPEC в plugins/ и (для
распространения через exe) допишите его в BUILTIN в plugins/__init__.py.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil

from omsreg.gui import plugins
from omsreg.gui.spec import UtilitySpec

log = logging.getLogger(__name__)


def discover() -> list[UtilitySpec]:
    """Собирает SPEC встроенных и дополнительно найденных плагинов (без дублей по id)."""
    seen: set[str] = set()
    specs: list[UtilitySpec] = []

    def add(module) -> None:
        spec = getattr(module, "SPEC", None)
        if isinstance(spec, UtilitySpec) and spec.id not in seen:
            seen.add(spec.id)
            specs.append(spec)

    # 1) встроенные (есть в exe)
    for module in getattr(plugins, "BUILTIN", ()):
        add(module)

    # 2) досканирование папки — работает при запуске из исходников
    for info in pkgutil.iter_modules(plugins.__path__):
        if info.name.startswith("_"):
            continue
        name = f"{plugins.__name__}.{info.name}"
        try:
            add(importlib.import_module(name))
        except Exception:
            # сбойный плагин не должен ни обрывать обход остальных, ни исчезать без следа
            log.warning("Плагин %s не загружен, пропущен", name, exc_info=True)

    specs.sort(key=lambda s: (s.order, s.title))
    return specs
