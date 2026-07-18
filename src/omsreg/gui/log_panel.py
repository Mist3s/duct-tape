"""Нижняя панель журнала: тёмное текстовое поле + индикатор работы + очистка."""

from __future__ import annotations

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
        sb = ttk.Scrollbar(box)
        sb.pack(side="right", fill="y")
        self.text = tk.Text(box, bg=C_LOG_BG, fg=C_LOG_FG, insertbackground=C_LOG_FG,
                            font=MONO_FONT, wrap="none", relief="flat",
                            yscrollcommand=sb.set, state="disabled", padx=8, pady=6)
        self.text.pack(side="left", fill="both", expand=True)
        sb.config(command=self.text.yview)
        self.text.tag_config("err", foreground="#ff8080")
        self.text.tag_config("ok", foreground="#8fe0a0")

    def write(self, line: str, tag: str | None = None) -> None:
        self.text.config(state="normal")
        if tag is None and "ошибк" in line.lower():
            tag = "err"
        self.text.insert("end", line + "\n", tag or ())
        self.text.see("end")
        self.text.config(state="disabled")

    def clear(self) -> None:
        self.text.config(state="normal")
        self.text.delete("1.0", "end")
        self.text.config(state="disabled")

    def start_progress(self) -> None:
        self.progress.pack(side="left", padx=(12, 0))
        self.progress.start(14)

    def stop_progress(self) -> None:
        self.progress.stop()
        self.progress.pack_forget()
