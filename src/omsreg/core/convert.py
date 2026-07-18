"""Преобразование строковых значений полей DBF в числа.

Важно: строгий разбор кода талона (as_code) и мягкий разбор целых/дробных
(as_int/as_float) — это РАЗНЫЕ функции по замыслу, а не дубли:

  * as_code принимает только чистые цифры (str.isdigit). Так код талона нельзя
    спутать с "-5", "+1" или значением с пробелами — от этого зависит, какие
    записи попадут под удаление, поэтому фильтр намеренно жёсткий.
  * as_int / as_float терпимы (int()/float() после strip и замены запятой на
    точку) — они разбирают коды отделений, исходы и стоимость в статистике, где
    встречаются знаки и десятичная запятая.
"""

from __future__ import annotations


def as_int(s: str) -> int | None:
    """Мягкий разбор целого: strip -> int(). None, если не число."""
    try:
        return int(s.strip())
    except ValueError:
        return None


def as_float(s: str) -> float | None:
    """Мягкий разбор дробного: strip, запятая -> точка, float(). None, если не число."""
    try:
        return float(s.strip().replace(",", "."))
    except ValueError:
        return None


def as_code(s: str) -> int | None:
    """Строгий разбор кода талона: только цифры -> int. None во всех остальных случаях."""
    s = s.strip()
    return int(s) if s.isdigit() else None
