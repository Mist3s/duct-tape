"""Общий словарь домена «стационар» для отчётных утилит stacionar и economics.

Справочники (типы стационара, исходы, названия отделений, наименования групп КСГ),
описание случая лечения (Case), классификация случая по отделению, группировка
случаев по признаку, разбор связанных настроек и повторяющиеся аргументы командной
строки. Тянет только omsreg.core и данные ksg_catalog, tkinter нет.
"""

from __future__ import annotations

from collections import defaultdict
from typing import NamedTuple

from omsreg.core import JobError
from omsreg.utils._shared.ksg_catalog import KSG

# коды исходов -> человекочитаемое название
ISHOD_NAMES = {
    1: "выписан",
    5: "перевод",
    7: "выписан с улучшением",
    9: "смерть",
    11: "самовольное прерывание лечения",
    12: "выписан без перемен",
}

# метки типов стационара
DAY_TYPE = "Дневной стационар"
ROUND_TYPE = "Круглосуточный стационар"

# Коды отделений дневного стационара по умолчанию — ЕДИНЫЙ источник истины для всех
# слоёв: аргумент --day-kotd, значения по умолчанию у run_stat/run_economics и поле
# вкладки в интерфейсе. Значение доменное, поэтому живёт здесь, а не в слое GUI.
DAY_KOTD_DEFAULT = "10,15,12"

# Названия отделений по коду KOTD — значение по умолчанию. Коды специфичны для
# учреждения, поэтому названия настраиваются (поле в интерфейсе / --kotd-names /
# аргумент kotd_names у run_*); этот словарь берётся, если ничего не задано.
KOTD_NAMES = {
    10: "Мусоргского",
    12: "ВОП",
    15: "Сельма",
    23: "Пульмонологическое",
    27: "Терапевтическое",
    61: "Неврологическое",
}


class Case(NamedTuple):
    """Один случай лечения — то, что утилиты читают из записи DBF стационара.

    Поля названы по именам полей DBF; st_type вычисляется из kotd функцией
    classify_type. Обращение по имени (c.stoim), а не по номеру в кортеже:
    вставка нового поля не ломает молча читателей.
    """

    st_type: str          # тип стационара: DAY_TYPE или ROUND_TYPE
    kotd: int | None      # код отделения (KOTD); None, если поле пустое
    kmkb: str             # код МКБ (KMKB) или «(без кода МКБ)»
    ishod: int | None     # код исхода (ISHOD); None, если поле пустое
    stoim: float          # стоимость случая (STOIM), 0.0 при пустом поле
    fact: float | None    # койко-дни (FACT); None, если поля нет в файле


def classify_type(kotd, day_kotd) -> str:
    """Тип стационара по коду отделения.

    Дневной, если kotd входит в day_kotd, иначе круглосуточный.
    """
    return DAY_TYPE if kotd in day_kotd else ROUND_TYPE


def group_by(cases, key) -> dict:
    """Раскладывает случаи по key(случай), сохраняя порядок первого появления группы.

    Один способ группировки для обоих отчётов стационара (по типу, отделению,
    коду МКБ, исходу): раньше этот цикл был написан в каждом из них свой.
    """
    groups = defaultdict(list)
    for c in cases:
        groups[key(c)].append(c)
    return groups


def normalize_fields(defaults: dict, overrides=None) -> dict:
    """Копия defaults с переопределением непустыми overrides.

    Единый разбор заданных пользователем имён полей DBF: значения обрезаются,
    приводятся к верхнему регистру, пустые игнорируются.
    """
    f = dict(defaults)
    if overrides:
        f.update({k: str(v).strip().upper() for k, v in overrides.items() if str(v).strip()})
    return f


def parse_day_kotd(day_kotd_str) -> set:
    """Разбирает список кодов дневного стационара '10,15;12' -> {10, 15, 12}."""
    try:
        return {int(x) for x in str(day_kotd_str).replace(";", ",").split(",") if x.strip()}
    except ValueError as e:
        raise JobError(f"Некорректный список кодов дневного стационара: {day_kotd_str}") from e


def parse_kotd_names(s) -> dict:
    """Разбирает строку настроек «код=название» в словарь {int(код): название}.

    Пары разделяются ';' или переводом строки; пустое и некорректное игнорируется.
    """
    names: dict[int, str] = {}
    for part in str(s or "").replace("\n", ";").split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        k, v = k.strip(), v.strip()
        if k.isdigit() and v:
            names[int(k)] = v
    return names


def format_kotd_names(names: dict) -> str:
    """Обратное к parse_kotd_names: словарь -> «код=название; код=название»."""
    return "; ".join(f"{k}={v}" for k, v in sorted(names.items()))


def ishod_name(code) -> str:
    """Код исхода с названием: '5 — перевод'. None -> '(не указан)'."""
    if code is None:
        return "(не указан)"
    return f"{code} — {ISHOD_NAMES.get(code, 'неизвестный исход')}"


def kotd_name(code, names=None) -> str:
    """Код отделения с названием, если оно известно: '27 — Терапевтическое'.

    names — словарь {код: название}; по умолчанию берётся встроенный KOTD_NAMES.
    """
    if code is None:
        return "?"
    name = (KOTD_NAMES if names is None else names).get(code)
    return f"{code} — {name}" if name else str(code)


def _ksg_lookup(code) -> tuple[str, str]:
    """(наименование, профиль) группы КСГ по коду; ('', '') для пустого/неизвестного."""
    if not code:
        return "", ""
    return KSG.get(str(code).strip().lower(), ("", ""))


def ksg_title(code) -> str:
    """Официальное наименование группы КСГ ('st05.001' -> 'Анемии (уровень 1)').

    Пустая строка, если код не из справочника (новый/неизвестный) или не задан.
    """
    return _ksg_lookup(code)[0]


def ksg_profile(code) -> str:
    """Профиль медпомощи группы КСГ ('st05.001' -> 'Гематология'); '' если неизвестен."""
    return _ksg_lookup(code)[1]


# --- повторяющиеся аргументы командной строки ---

def add_kotd_args(parser) -> None:
    """Добавляет к парсеру общие для отчётных утилит --day-kotd и --kotd-names."""
    parser.add_argument("--day-kotd", default=DAY_KOTD_DEFAULT,
                        help="коды отделений дневного стационара через запятую "
                             f"(по умолчанию {DAY_KOTD_DEFAULT})")
    parser.add_argument("--kotd-names", default=None,
                        help="названия отделений: «23=Пульмонологическое; 27=Терапевтическое» "
                             "(по умолчанию встроенные)")


def resolve_kotd_names(args):
    """Разбирает --kotd-names в словарь; None, если аргумент не задан."""
    return parse_kotd_names(args.kotd_names) if args.kotd_names else None
