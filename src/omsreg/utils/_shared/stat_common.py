"""Общий словарь домена «стационар» для отчётных утилит stacionar и economics.

Справочники (типы стационара, исходы, названия отделений), классификация случая по
отделению, разбор связанных настроек и повторяющиеся аргументы командной строки.
Импортирует только omsreg.core, tkinter не тянет.
"""

from __future__ import annotations

from omsreg.core import JobError

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


def classify_type(kotd, day_kotd) -> str:
    """Тип стационара по коду отделения: дневной, если kotd входит в day_kotd,
    иначе круглосуточный."""
    return DAY_TYPE if kotd in day_kotd else ROUND_TYPE


def normalize_fields(defaults: dict, overrides=None) -> dict:
    """Копия defaults с переопределением непустыми overrides (обрезка + верхний
    регистр) — единый разбор заданных пользователем имён полей DBF."""
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
    """Разбирает строку настроек «код=название» (пары через ';' или перевод строки)
    в словарь {int(код): название}. Пустое/некорректное игнорируется."""
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
    names — словарь {код: название}; по умолчанию берётся встроенный KOTD_NAMES."""
    if code is None:
        return "?"
    name = (KOTD_NAMES if names is None else names).get(code)
    return f"{code} — {name}" if name else str(code)


# --- повторяющиеся аргументы командной строки ---

def add_kotd_args(parser) -> None:
    parser.add_argument("--day-kotd", default="10,15,12",
                        help="коды отделений дневного стационара через запятую (по умолчанию 10,15,12)")
    parser.add_argument("--kotd-names", default=None,
                        help="названия отделений: «23=Пульмонологическое; 27=Терапевтическое» "
                             "(по умолчанию встроенные)")


def resolve_kotd_names(args):
    """Разбирает --kotd-names в словарь; None, если аргумент не задан."""
    return parse_kotd_names(args.kotd_names) if args.kotd_names else None
