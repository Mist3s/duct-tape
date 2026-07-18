"""Реестр утилит: сбор всех UtilitySpec из пакета omsreg.gui.plugins.

Добавление утилиты не требует правок здесь — достаточно положить модуль-плагин в
omsreg.gui.plugins с объектом SPEC (UtilitySpec). discover() найдёт его сам.
"""

from __future__ import annotations

import importlib
import pkgutil

from omsreg.gui.spec import UtilitySpec


def discover() -> list[UtilitySpec]:
    """Импортирует все модули omsreg.gui.plugins и собирает их SPEC, сортируя по order/title."""
    from omsreg.gui import plugins

    specs: list[UtilitySpec] = []
    for mod_info in pkgutil.iter_modules(plugins.__path__):
        if mod_info.name.startswith("_"):
            continue
        mod = importlib.import_module(f"{plugins.__name__}.{mod_info.name}")
        spec = getattr(mod, "SPEC", None)
        if isinstance(spec, UtilitySpec):
            specs.append(spec)
    specs.sort(key=lambda s: (s.order, s.title))
    return specs
