"""Справочник КСГ: загрузка каталога и доступ по коду группы (наименование, профиль)."""

from omsreg.utils._shared.ksg_catalog import KSG
from omsreg.utils._shared.stat_common import ksg_profile, ksg_title


def test_catalog_loaded():
    assert len(KSG) > 600                                  # дневной + круглосуточный (692 на 2026)
    assert all(code == code.lower() for code in KSG)       # коды хранятся в нижнем регистре
    assert all(code[:2] in ("st", "ds") for code in KSG)   # st… — круглосуточный, ds… — дневной
    name, profile = KSG["st05.001"]
    assert name and profile                                # значения непустые


def test_ksg_title_and_profile():
    assert ksg_title("st05.001") == "Анемии (уровень 1)"
    assert ksg_profile("st05.001") == "Гематология"


def test_ksg_lookup_normalizes_code():
    assert ksg_title(" ST05.001 ") == "Анемии (уровень 1)"   # регистр и пробелы не важны


def test_ksg_lookup_unknown_is_empty():
    assert ksg_title("нет-такой-группы") == ""
    assert ksg_profile("нет-такой-группы") == ""
    assert ksg_title(None) == "" and ksg_title("") == ""
