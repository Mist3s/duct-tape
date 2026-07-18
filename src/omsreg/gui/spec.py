"""Декларативные спецификации утилит — контракт платформы.

Одна утилита = один UtilitySpec: метаданные + схема параметров (ParamSpec) +
кнопки-действия (ActionSpec) + функция run(RunContext) -> JobResult. Приложение
(omsreg.gui.app) строит из этого вкладку, хранение настроек и весь поток запуска,
поэтому в самих плагинах нет ни одной строки Tkinter.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable


class ParamKind(Enum):
    DIR = "dir"     # папка   -> поле ввода + «Обзор…» (askdirectory)
    FILE = "file"   # файл    -> поле ввода + «Обзор…» (askopenfilename)
    PATH = "path"   # файл ИЛИ папка -> поле ввода + две кнопки «Файл…»/«Папка…»
    TEXT = "text"   # строка  -> ttk.Entry
    INT = "int"     # целое   -> ttk.Spinbox
    BOOL = "bool"   # флажок  -> ttk.Checkbutton


@dataclass(frozen=True)
class ParamSpec:
    key: str                                   # ключ в params и (с префиксом id) в настройках
    label: str
    kind: ParamKind = ParamKind.TEXT
    default: Any = ""
    required: bool = False
    hint: str = ""                             # серая подсказка под полем
    advanced: bool = False                     # уходит под разделитель «Дополнительно»
    persist: bool = True                       # сохранять ли в настройки
    filetypes: tuple = ()                      # для FILE/PATH: (("DBF", "*.dbf"),)
    min: int = 1                               # для INT (ttk.Spinbox from_)
    max: int = 999_999                         # для INT (ttk.Spinbox to)
    group: str | None = None                # поля одного group кладутся сеткой 2-в-ряд
    width: int | None = None                # фикс. ширина поля ввода
    require_msg: str | None = None          # текст предупреждения, если пусто
    legacy_key: str | None = None           # старый ключ из настройки.txt (миграция)

    def config_key(self, util_id: str) -> str:
        return f"{util_id}.{self.key}"


@dataclass(frozen=True)
class ActionSpec:
    key: str                                   # "dry" / "delete" / "build"
    label: str
    style: str = "Accent.TButton"              # Accent / Danger / Ghost
    destructive: bool = False                  # True -> окно подтверждения перед запуском
    inject: dict = field(default_factory=dict)  # доп. значения в params, напр. {"dry": True}


@dataclass
class RunContext:
    params: dict                               # {param.key: значение} + action.inject
    action: ActionSpec
    log_handler: logging.Handler               # прицепить к логгеру задачи -> живой журнал


@dataclass
class JobResult:
    summary: str                               # одна строка: в журнал и в messagebox
    log_text: str = ""                         # необяз. крупный текст в журнал (отчёт stat)
    had_error: bool = False
    open_path: Path | None = None           # файл, который предложить открыть (HTML/лог)
    box_kind: str = "info"                     # info | warning | error


@dataclass(frozen=True)
class UtilitySpec:
    id: str                                     # стабильный id: namespace настроек + id вкладки
    title: str                                  # заголовок вкладки
    description: str                            # абзац-подсказка вверху вкладки
    params: tuple[ParamSpec, ...]
    actions: tuple[ActionSpec, ...]
    run: Callable[[RunContext], JobResult]
    order: int = 100                            # порядок вкладок
    validate: Callable[[dict], str | None] | None = None       # кросс-проверка полей
    confirm_message: Callable[[dict], str] | None = None          # текст подтверждения
