"""Платформа GUI без дисплея: реестр плагинов, файл настроек, раскраска журнала.

Окна здесь не создаются (в среде тестов нет дисплея) — проверяется только логика,
которая живёт в модулях omsreg.gui: устойчивость обхода плагинов к сбойному модулю,
диагностика непонятых строк настроек, выбор цвета строки журнала по уровню записи и
единый текст подтверждения удаления.
"""

import logging
import queue

from omsreg.core import QueueLogHandler
from omsreg.gui import app as gui_app
from omsreg.gui import config as cfg
from omsreg.gui import registry
from omsreg.gui.log_panel import level_tag
from omsreg.gui.plugins import codes, talons
from omsreg.gui.spec import BoxKind, JobResult, ParamKind, ParamSpec


class _Info:
    """Минимальная замена pkgutil.ModuleInfo — нужен только name."""

    def __init__(self, name):
        self.name = name


class _FakeVar:
    """Заменитель tk.Variable: значения полей без окна и без дисплея."""

    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class _FakeLog:
    """Заменитель панели журнала: запоминает (tag, строка)."""

    def __init__(self):
        self.lines = []

    def write(self, line, tag=None):
        self.lines.append((tag, line))


class _FakeApp:
    """Достаточный «self» для методов App, которым нужны только поля и журнал."""

    def __init__(self, items=(), log_handler=None):
        self._items = list(items)
        self.log = _FakeLog()
        self.log_handler = log_handler

    def _iter_config(self):
        return iter(self._items)


def test_discover_returns_builtin_specs():
    ids = [s.id for s in registry.discover()]
    assert ids == ["talons", "codes", "stat", "economics", "deaths"]


def test_discover_survives_broken_plugin(monkeypatch, caplog):
    """Сбойный плагин: предупреждение в лог, обход остальных не обрывается."""
    imported = []
    real_import = registry.importlib.import_module

    def fake_import(name):
        if name.endswith(".broken"):
            raise ImportError("сломан нарочно")
        imported.append(name)
        return real_import(name)

    monkeypatch.setattr(registry.pkgutil, "iter_modules",
                        lambda _path: [_Info("broken"), _Info("codes")])
    monkeypatch.setattr(registry.importlib, "import_module", fake_import)

    with caplog.at_level(logging.WARNING, logger="omsreg.gui.registry"):
        specs = registry.discover()

    assert imported == ["omsreg.gui.plugins.codes"]  # обход продолжился после сбоя
    assert "omsreg.gui.plugins.broken" in caplog.text
    assert "сломан нарочно" in caplog.text  # трассировка попала в журнал
    assert [s.id for s in specs] == ["talons", "codes", "stat", "economics", "deaths"]


def test_read_kv_reports_lines_without_equals(tmp_path):
    path = tmp_path / "настройки.txt"
    path.write_text("# комментарий\n\ncodes.min_len = 6\nтут забыли знак\n", encoding="utf-8")
    data, problems = cfg.read_kv(path)
    assert data == {"codes.min_len": "6"}
    assert problems == ["строка 4: тут забыли знак"]


def test_read_kv_without_problems(tmp_path):
    path = tmp_path / "настройки.txt"
    path.write_text("\n".join(cfg.HEADER_LINES) + "\ncodes.field = SN_TAL\n", encoding="utf-8")
    data, problems = cfg.read_kv(path)
    assert data == {"codes.field": "SN_TAL"}
    assert problems == []


def test_level_tag_by_record_level():
    assert level_tag("12:00:01  ERROR   не удалось прочитать файл") == "err"
    assert level_tag("12:00:01  CRITICAL всё плохо") == "err"
    assert level_tag("12:00:01  WARNING коды вне справочника") == "warn"
    assert level_tag("12:00:01  INFO    Готово") is None


def test_level_tag_ignores_word_error_in_info_message():
    """Штатные INFO про «ошибочные талоны» больше не красятся как ошибка."""
    assert level_tag("12:00:01  INFO    Найдено ошибочных талонов: 12") is None
    assert level_tag("Отчёт: ошибки не найдены") is None


def test_default_confirm_is_single_text_for_destructive_plugins():
    with_dir = gui_app.App._default_confirm({"dir": "/данные/ОМС"})
    assert with_dir == (
        "Будут БЕЗВОЗВРАТНО удалены записи из DBF-файлов в папке:\n/данные/ОМС\n\n"
        "Перед изменением каждого файла создаётся резервная копия (папка backup_…).\n\n"
        "Продолжить удаление?"
    )
    assert gui_app.App._default_confirm({}).startswith(
        "Будут БЕЗВОЗВРАТНО удалены записи из DBF-файлов.\n\n")
    # оба разрушающих плагина полагаются на общий текст платформы
    for module in (talons, codes):
        assert module.SPEC.confirm_message is None
        assert any(a.destructive for a in module.SPEC.actions)


def test_message_boxes_cover_all_box_kinds():
    assert set(gui_app.MESSAGE_BOXES) == set(BoxKind)
    assert JobResult("готово").box_kind is BoxKind.INFO


def _int_param(key="min_len"):
    return ParamSpec(key, "Длина кода, цифр: от", ParamKind.INT, default=6, min=1, max=20)


def test_load_config_warns_about_non_numeric_value(tmp_path, monkeypatch):
    """Битое число в настройках: значение по умолчанию + предупреждение, а не тишина."""
    path = tmp_path / "настройки.txt"
    path.write_text("codes.min_len = шесть\n", encoding="utf-8")
    monkeypatch.setattr(cfg, "config_path", lambda: path)
    stub = _FakeApp([("codes", _int_param(), _FakeVar(6))])

    gui_app.App._load_config(stub)

    warns = [line for tag, line in stub.log.lines if tag == "warn"]
    assert len(warns) == 1
    assert "codes.min_len" in warns[0] and "нужно число" in warns[0]
    assert stub._items[0][2].get() == 6  # осталось значение по умолчанию
    assert any("Настройки загружены" in line for _t, line in stub.log.lines)


def test_load_config_warns_about_line_without_equals(tmp_path, monkeypatch):
    path = tmp_path / "настройки.txt"
    path.write_text("codes.min_len = 8\nмусор без знака\n", encoding="utf-8")
    monkeypatch.setattr(cfg, "config_path", lambda: path)
    stub = _FakeApp([("codes", _int_param(), _FakeVar(6))])

    gui_app.App._load_config(stub)

    warns = [line for tag, line in stub.log.lines if tag == "warn"]
    assert warns == ["Настройки, строка не понята (нет «=»), пропущена — строка 2: мусор без знака"]
    assert stub._items[0][2].get() == 8  # исправное значение всё равно прочитано


def test_job_logger_finds_logger_of_running_task():
    """log.exception должен уходить в логгер задачи — там и панель, и .log-файл."""
    handler = QueueLogHandler(queue.Queue())
    job = logging.getLogger("omsreg.utils.тестовая_задача")
    job.addHandler(handler)
    try:
        assert gui_app.App._job_logger(_FakeApp(log_handler=handler)) is job
    finally:
        job.removeHandler(handler)


def test_job_logger_falls_back_to_ui_logger_and_keeps_traceback():
    q = queue.Queue()
    handler = QueueLogHandler(q)
    logger = gui_app.App._job_logger(_FakeApp(log_handler=handler))
    try:
        assert logger is gui_app.log
        try:
            raise ValueError("бум")
        except ValueError:
            logger.exception("Внутренняя ошибка при выполнении задачи")
        kind, line = q.get_nowait()
        assert kind == "log"
        assert "Traceback" in line and "ValueError: бум" in line
    finally:
        gui_app.log.removeHandler(handler)
