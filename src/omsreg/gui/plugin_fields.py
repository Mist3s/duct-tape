"""Общие поля вкладок для отчётных утилит стационара (статистика и экономика).

Обе утилиты читают одни и те же DBF, поэтому первые три поля вкладки (путь,
коды отделений дневного стационара, названия отделений) и устройство полей с
именами полей DBF у них совпадают — здесь они описаны один раз.

Модуль лежит на уровне платформы (рядом с spec, registry, theme), а НЕ в пакете
plugins: там по соглашению один файл — одна вкладка, и каждый модуль объявляет SPEC.
Доменные значения (названия отделений, коды дневного стационара) сюда не дублируются,
а берутся из omsreg.utils — единого источника истины домена.
"""

from __future__ import annotations

from omsreg.gui.spec import ParamKind, ParamSpec
from omsreg.utils import DAY_KOTD_DEFAULT, KOTD_NAMES, format_kotd_names


def target_param() -> ParamSpec:
    """Поле «DBF-файл или папка» — источник данных отчёта."""
    return ParamSpec("target", "DBF-файл или папка:", ParamKind.PATH, required=True,
                     filetypes=(("DBF", "*.dbf"),),
                     require_msg="Укажите DBF-файл или папку.", legacy_key="статистика_путь")


def day_kotd_param() -> ParamSpec:
    """Поле «Коды отделений ДС» — остальные отделения считаются круглосуточными."""
    return ParamSpec("day_kotd", "Коды отделений ДС:", ParamKind.TEXT,
                     default=DAY_KOTD_DEFAULT, width=18,
                     hint="через запятую; остальные отделения — круглосуточный стационар",
                     legacy_key="дневной_стационар_коды")


def kotd_names_param() -> ParamSpec:
    """Поле «Названия отделений» — справочник КОТД -> название для отчёта."""
    return ParamSpec("kotd_names", "Названия отделений:", ParamKind.TEXT, advanced=True,
                     default=format_kotd_names(KOTD_NAMES),
                     hint="формат: 23=Пульмонологическое; 27=Терапевтическое; 61=Неврологическое",
                     legacy_key="названия_отделений")


def dbf_field_param(key: str, label: str, default: str, width: int,
                    legacy_key: str | None = None) -> ParamSpec:
    """Поле с именем поля DBF — уходит под разделитель «Дополнительно», сеткой 2-в-ряд."""
    return ParamSpec(key, label, ParamKind.TEXT, default=default, advanced=True,
                     group="fields", width=width, legacy_key=legacy_key)
