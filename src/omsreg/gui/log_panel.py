"""Нижняя панель журнала: тёмное текстовое поле + индикатор работы + очистка.

Здесь же живёт autohide_scrollbar — общий помощник автоскрытия полосы прокрутки,
который нужен и панели журнала, и списку утилит, и многострочным полям (app.py).
"""

from __future__ import annotations

import re
import tkinter as tk
from tkinter import ttk

from omsreg.gui.theme import (
    C_BG,
    C_BORDER,
    C_INK2,
    C_LOG_BG,
    C_LOG_FG,
    MONO_FONT,
    UI_FONT_B,
)

# Строка из журнала задачи имеет вид «ЧЧ:ММ:СС  LEVELNAME сообщение» — формат задан в
# omsreg.core.logging_setup.setup_job_logging. Цвет берётся по уровню записи, а не по
# подстроке «ошибк»: штатные сообщения уровня INFO про ошибочные талоны — не ошибки.
_LOG_LINE_RE = re.compile(r"^\d\d:\d\d:\d\d\s+([A-Z]+)\s")
_LEVEL_TAGS = {"WARNING": "warn", "ERROR": "err", "CRITICAL": "err"}


def level_tag(line: str) -> str | None:
    """Тег раскраски строки журнала по уровню записи (None — обычная строка)."""
    m = _LOG_LINE_RE.match(line)
    return _LEVEL_TAGS.get(m.group(1)) if m else None


def autohide_scrollbar(sb: ttk.Scrollbar, side: str = "left", before: tk.Misc | None = None):
    """Готовит yscrollcommand: полоса прокрутки видна, только если есть что прокручивать.

    Один помощник на все три прокручиваемых области платформы; side и before —
    единственное, чем они различались.
    """
    def on_scroll(first, last) -> None:
        if float(first) <= 0.0 and float(last) >= 1.0:
            sb.pack_forget()
        elif not sb.winfo_ismapped():
            opts = {"side": side, "fill": "y"}
            if before is not None:
                opts["before"] = before
            sb.pack(**opts)
        sb.set(first, last)

    return on_scroll


class LogPanel:
    """Журнал работы. Строки добавляются из главного потока (метод write)."""

    def __init__(self, parent: tk.Misc):
        frame = tk.Frame(parent, bg=C_BG)
        frame.pack(fill="both", expand=True, padx=12, pady=(0, 10))
        top = tk.Frame(frame, bg=C_BG)
        top.pack(fill="x")
        tk.Label(top, text="Журнал работы", bg=C_BG, fg=C_INK2, font=UI_FONT_B).pack(side="left")
        ttk.Button(top, text="Очистить", style="Ghost.TButton", command=self.clear).pack(side="right")
        # индикатор работы: появляется рядом с заголовком только во время выполнения
        self.progress = ttk.Progressbar(top, mode="indeterminate", length=160)

        box = tk.Frame(frame, bg=C_LOG_BG, highlightthickness=1, highlightbackground=C_BORDER)
        box.pack(fill="both", expand=True, pady=(4, 0))
        self._sb = ttk.Scrollbar(box, style="Log.Vertical.TScrollbar")
        self.text = tk.Text(box, bg=C_LOG_BG, fg=C_LOG_FG, insertbackground=C_LOG_FG,
                            font=MONO_FONT, wrap="none", relief="flat",
                            state="disabled", padx=8, pady=6)
        self.text.configure(yscrollcommand=autohide_scrollbar(self._sb, "right", before=self.text))
        self.text.pack(side="left", fill="both", expand=True)
        self._sb.config(command=self.text.yview)
        self.text.tag_config("err", foreground="#ff8080")
        self.text.tag_config("warn", foreground="#f0c674")
        self.text.tag_config("ok", foreground="#8fe0a0")

    def write(self, line: str, tag: str | None = None) -> None:
        """Добавляет строку; без явного tag цвет определяется уровнем записи журнала."""
        self.text.config(state="normal")
        if tag is None:
            tag = level_tag(line)
        self.text.insert("end", line + "\n", tag or ())
        self.text.see("end")
        self.text.config(state="disabled")

    def clear(self) -> None:
        """Очищает журнал перед новым запуском."""
        self.text.config(state="normal")
        self.text.delete("1.0", "end")
        self.text.config(state="disabled")

    def start_progress(self) -> None:
        """Показывает и запускает индикатор выполнения."""
        self.progress.pack(side="left", padx=(12, 0))
        self.progress.start(14)

    def stop_progress(self) -> None:
        """Останавливает и убирает индикатор выполнения."""
        self.progress.stop()
        self.progress.pack_forget()
