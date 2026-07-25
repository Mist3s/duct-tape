"""Справочник КСГ: каталог, доступ по коду группы (наименование, профиль) и его генератор."""

import importlib.util
from pathlib import Path

from omsreg.utils._shared import ksg_catalog
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


def test_catalog_docstring_is_clean():
    doc = ksg_catalog.__doc__
    assert doc and "#" not in doc                 # решётки внутри докстринга — мусор от генератора
    assert "\n\nПересобрать" in doc               # перед абзацем есть пустая строка (PEP 257)


def _load_builder():
    """Загружает tools/build_ksg_catalog.py как модуль (пакетом он не является)."""
    path = Path(__file__).resolve().parents[1] / "tools" / "build_ksg_catalog.py"
    spec = importlib.util.spec_from_file_location("build_ksg_catalog", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_render_generates_valid_docstring():
    builder = _load_builder()
    text = builder.render({"st00.001": ("Название", "Профиль")},
                          [Path("/x/дневной.xlsx"), Path("/x/круглосуточный.xlsx")])
    ns: dict = {}
    exec(compile(text, "ksg_catalog.py", "exec"), ns)  # сгенерированный модуль должен исполняться
    assert ns["KSG"] == {"st00.001": ("Название", "Профиль")}
    doc = ns["__doc__"]
    assert "#" not in doc
    assert "  - дневной.xlsx\n  - круглосуточный.xlsx\n\nПересобрать" in doc
