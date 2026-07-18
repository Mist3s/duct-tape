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
import pkgutil

from omsreg.gui.spec import UtilitySpec


def discover() -> list[UtilitySpec]:
    """Собирает SPEC встроенных и дополнительно найденных плагинов (без дублей по id)."""
    from omsreg.gui import plugins

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
    try:
        for info in pkgutil.iter_modules(plugins.__path__):
            if not info.name.startswith("_"):
                add(importlib.import_module(f"{plugins.__name__}.{info.name}"))
    except Exception:
        pass

    specs.sort(key=lambda s: (s.order, s.title))
    return specs
