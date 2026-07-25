"""Справочник распределения причин смерти по группам отчёта «Смертность».

Загружает правила «диапазон кодов МКБ-10 → группа (+ подгруппа)» из
mkb_death_groups.csv (лежит рядом с модулем) и классифицирует код МКБ. Правила
применяются сверху вниз, берётся первое подходящее по диапазону — поэтому
специфичные правила стоят выше общих (см. комментарии в CSV).

Диапазон сопоставляется по букве и ЦЕЛОЙ части кода: правило I20–I25 покрывает
I20.0…I25.9. Можно передать путь к своему CSV того же формата (правка справочника
без пересборки программы) — иначе берётся встроенный.

Только распределение по группам; сами данные об умерших — во входном отчёте.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path
from typing import NamedTuple

RESOURCE = "mkb_death_groups.csv"


class Rule(NamedTuple):
    lo: tuple        # нижняя граница как (буква, целая часть): I20 -> ('I', 20)
    hi: tuple        # верхняя граница: I25 -> ('I', 25); диапазон может пересекать буквы (C00–D48)
    group: str       # название группы (столбца) в отчёте
    subgroup: str    # подгруппа для приписки «(в т.ч. …)» или ''


def _read_text(path=None) -> str:
    """Текст CSV: из внешнего файла (если задан) либо встроенного ресурса пакета.
    В .exe (PyInstaller) importlib.resources может подвести — тогда читаем файл рядом с модулем."""
    if path:
        return Path(path).read_text(encoding="utf-8-sig")
    try:
        return resources.files("omsreg.utils._shared").joinpath(RESOURCE).read_text(encoding="utf-8-sig")
    except (FileNotFoundError, ModuleNotFoundError, OSError):
        return (Path(__file__).with_name(RESOURCE)).read_text(encoding="utf-8-sig")


def _parse_bound(code: str):
    """Код МКБ как сравнимый ключ (буква, ЦЕЛАЯ часть): 'I20' -> ('I', 20),
    'I25.5' -> ('I', 25). Цифры берутся до первого не-цифрового символа (точки).
    Пусто/битое -> None. Ключи сравниваются как кортежи — это и есть порядок кодов
    МКБ (сначала буква, потом номер), поэтому диапазон может пересекать буквы (C00–D48)."""
    code = (code or "").strip().upper()
    if len(code) < 2 or not code[0].isalpha():
        return None
    digits = ""
    for c in code[1:]:
        if c.isdigit():
            digits += c
        else:
            break
    return (code[0], int(digits)) if digits else None


def load_rules(path=None) -> list[Rule]:
    """Разбирает CSV в список правил (в порядке файла). Пустые строки и '#'-комментарии
    пропускаются; строка-заголовок «код_от;…» тоже. Кривые строки игнорируются."""
    rules: list[Rule] = []
    for raw in _read_text(path).splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split(";")]
        if len(parts) < 3:
            continue
        lo, hi = _parse_bound(parts[0]), _parse_bound(parts[1])
        if lo is None or hi is None or hi < lo:
            continue  # заголовок «код_от;код_до;…» и мусор отсеиваются здесь
        subgroup = parts[3] if len(parts) > 3 else ""
        rules.append(Rule(lo, hi, parts[2], subgroup))
    return rules


def classify(code: str, rules: list[Rule]) -> tuple[str, str]:
    """(группа, подгруппа) для кода МКБ по первому подходящему правилу.
    ('', '') — код пуст или не покрыт справочником (в отчёте это «Прочие»)."""
    key = _parse_bound(code)
    if key is None:
        return "", ""
    for r in rules:
        if r.lo <= key <= r.hi:
            return r.group, r.subgroup
    return "", ""


def group_order(rules: list[Rule]) -> list[str]:
    """Группы в порядке первого появления в справочнике — задаёт порядок столбцов."""
    seen: list[str] = []
    for r in rules:
        if r.group not in seen:
            seen.append(r.group)
    return seen


def subgroup_order(rules: list[Rule], group: str) -> list[str]:
    """Непустые подгруппы группы в порядке появления — задаёт порядок в «(в т.ч. …)»."""
    seen: list[str] = []
    for r in rules:
        if r.group == group and r.subgroup and r.subgroup not in seen:
            seen.append(r.subgroup)
    return seen
