"""Справочник распределения причин смерти по группам отчёта «Смертность».

Загружает правила «диапазон кодов МКБ-10 → группа (+ подгруппа)» из
mkb_death_groups.csv (лежит рядом с модулем) и классифицирует код МКБ. Правила
применяются сверху вниз, берётся первое подходящее по диапазону — поэтому
специфичные правила стоят выше общих (см. комментарии в CSV).

Диапазон сопоставляется по букве и ЦЕЛОЙ части кода: правило I20–I25 покрывает
I20.0…I25.9. Можно передать путь к своему CSV того же формата (правка справочника
без пересборки программы) — иначе берётся встроенный.

Справочник правится руками, поэтому опечатка в нём не имеет права пройти молча:
load_rules возвращает вторым значением список замечаний по непонятным строкам, а
границы диапазонов разбираются ЦЕЛИКОМ (мусорный хвост — ошибка, а не «почти код»).
Замечания обязан показать вызывающий (см. stat_deaths.run_deaths).

Только распределение по группам; сами данные об умерших — во входном отчёте.
"""

from __future__ import annotations

import re
from importlib import resources
from pathlib import Path
from typing import NamedTuple

RESOURCE = "mkb_death_groups.csv"

# минимум полей в строке правила: код_от;код_до;группа (подгруппа необязательна)
MIN_FIELDS = 3

# граница диапазона в правиле: буква + 1–3 цифры (+ необязательный подкод после точки)
_BOUND_RE = re.compile(r"([A-ZА-Я])(\d{1,3})(?:\.\d+)?")
# код МКБ в данных: та же форма, но после кода допускается пояснительный текст
# («I25.5 Атеросклеротическая болезнь сердца»); буква или цифра вплотную к номеру —
# это опечатка («I2O» с латинской O вместо нуля), а не код
_CODE_RE = re.compile(r"([A-ZА-Я])(\d{1,3})(?:\.\d+)?(?![0-9A-ZА-Я])")


class Rule(NamedTuple):
    lo: tuple        # нижняя граница как (буква, целая часть): I20 -> ('I', 20)
    hi: tuple        # верхняя граница: I25 -> ('I', 25); диапазон может пересекать буквы (C00–D48)
    group: str       # название группы (столбца) в отчёте
    subgroup: str    # подгруппа для приписки «(в т.ч. …)» или ''


def _read_text(path=None) -> str:
    """Текст CSV: из внешнего файла (если задан) либо встроенного ресурса пакета.

    В .exe (PyInstaller) importlib.resources может подвести — тогда читаем файл
    рядом с модулем.
    """
    if path:
        return Path(path).read_text(encoding="utf-8-sig")
    try:
        return resources.files("omsreg.utils._shared").joinpath(RESOURCE).read_text(encoding="utf-8-sig")
    except (FileNotFoundError, ModuleNotFoundError, OSError):
        return (Path(__file__).with_name(RESOURCE)).read_text(encoding="utf-8-sig")


def _key(match) -> tuple:
    """(буква, ЦЕЛАЯ часть) из разобранного кода: 'I25.5' -> ('I', 25)."""
    return (match.group(1), int(match.group(2)))


def _parse_bound(code: str):
    """Граница диапазона правила как сравнимый ключ (буква, ЦЕЛАЯ часть).

    'I20' -> ('I', 20), 'I25.5' -> ('I', 25). Код разбирается ЦЕЛИКОМ: мусорный
    хвост даёт None, а не правдоподобно неверный диапазон (опечатка «I2O» с
    латинской O вместо нуля иначе превратилась бы в I02 и перекрыла бы половину
    класса I). Ключи сравниваются как кортежи — это и есть порядок кодов МКБ
    (сначала буква, потом номер), поэтому диапазон может пересекать буквы (C00–D48).
    """
    m = _BOUND_RE.fullmatch((code or "").strip().upper())
    return _key(m) if m else None


def _parse_code(code: str):
    """Код МКБ из ячейки диагноза как сравнимый ключ (буква, ЦЕЛАЯ часть).

    В отличие от границы правила, после кода допускается пояснительный текст
    («I25.5 Атеросклеротическая болезнь сердца»), но не буква и не цифра вплотную
    к номеру. None — пусто или не похоже на код МКБ (в отчёте это «Прочие»).
    """
    m = _CODE_RE.match((code or "").strip().upper())
    return _key(m) if m else None


def _is_header(line: str) -> bool:
    """Строка-заголовок таблицы («код_от;код_до;группа;подгруппа») — не правило и не мусор."""
    first = line.split(";", 1)[0].strip().lower().replace("_", "").replace(" ", "")
    return first == "кодот"


def load_rules(path=None) -> tuple[list[Rule], list[str]]:
    """Разбирает CSV в список правил (в порядке файла).

    Возвращает (правила, замечания). Пустые строки, '#'-комментарии и строка-
    заголовок «код_от;код_до;…» пропускаются молча — это не мусор. Любая другая
    непонятная строка в правила НЕ попадает, но и не теряется бесследно: о ней
    сообщается в замечаниях (с номером строки и текстом), иначе смерти ушли бы
    в «Прочие» без намёка на причину.
    """
    rules: list[Rule] = []
    problems: list[str] = []
    for n, raw in enumerate(_read_text(path).splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#") or _is_header(line):
            continue
        parts = [p.strip() for p in line.split(";")]
        if len(parts) < MIN_FIELDS:
            problems.append(f"строка {n}: «{line}» — мало полей, нужно "
                            "код_от;код_до;группа[;подгруппа]")
            continue
        lo, hi = _parse_bound(parts[0]), _parse_bound(parts[1])
        if lo is None or hi is None:
            bad = ", ".join(f"{name} «{text}»" for name, key, text
                            in (("код_от", lo, parts[0]), ("код_до", hi, parts[1])) if key is None)
            problems.append(f"строка {n}: «{line}» — не разобран {bad} "
                            "(ожидается буква и 1–3 цифры, например I20 или I25.5)")
            continue
        if hi < lo:
            problems.append(f"строка {n}: «{line}» — код_до меньше код_от")
            continue
        if not parts[2]:
            problems.append(f"строка {n}: «{line}» — не указана группа (третье поле)")
            continue
        subgroup = parts[3] if len(parts) > MIN_FIELDS else ""
        rules.append(Rule(lo, hi, parts[2], subgroup))
    return rules, problems


def classify(code: str, rules: list[Rule]) -> tuple[str, str]:
    """(группа, подгруппа) для кода МКБ по первому подходящему правилу.

    ('', '') — код пуст, не похож на код МКБ или не покрыт справочником
    (в отчёте это «Прочие»).
    """
    key = _parse_code(code)
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
