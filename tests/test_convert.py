"""Строгий разбор кода талона vs мягкий разбор чисел статистики."""

from omsreg.core.convert import as_code, as_float, as_int


def test_as_code_strict_digits_only():
    assert as_code("007") == 7
    assert as_code("  59371636 ") == 59371636
    assert as_code("-5") is None       # знак — не код
    assert as_code("+1") is None
    assert as_code("12a") is None
    assert as_code("") is None


def test_as_int_lenient():
    assert as_int(" 10 ") == 10
    assert as_int("-5") == -5          # мягкий разбор допускает знак
    assert as_int("x") is None


def test_as_float_comma_and_dot():
    assert as_float("1500,50") == 1500.5
    assert as_float("1500.50") == 1500.5
    assert as_float(" -3,0 ") == -3.0
    assert as_float("нет") is None
