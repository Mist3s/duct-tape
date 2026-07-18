"""Цветовая палитра, шрифты и ttk-стили графического интерфейса."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

APP_TITLE = "Обработка реестров ОМС"

# ---- цветовая палитра ----
C_HEADER = "#1f2b3a"
C_HEADER_SUB = "#9fb3cc"
C_BG = "#f4f6fa"
C_CARD = "#ffffff"
C_INK = "#0b0b0b"
C_INK2 = "#52514e"
C_ACCENT = "#2a78d6"
C_ACCENT_D = "#1f5fb0"
C_DANGER = "#c23b3b"
C_DANGER_D = "#9e2f2f"
C_BORDER = "#d8dce6"
C_LOG_BG = "#11151c"
C_LOG_FG = "#e6e6ef"
C_TAB_IDLE = "#dfe5ef"

UI_FONT = ("Segoe UI", 10)
UI_FONT_B = ("Segoe UI", 10, "bold")
TITLE_FONT = ("Segoe UI Semibold", 15, "bold")
MONO_FONT = ("Consolas", 10)


def build_styles(root: tk.Misc) -> None:
    """Настраивает ttk-стили (кнопки, поля, вкладки, индикатор прогресса)."""
    st = ttk.Style(root)
    try:
        st.theme_use("clam")
    except tk.TclError:
        pass
    st.configure("TFrame", background=C_BG)
    st.configure("Card.TFrame", background=C_CARD)
    st.configure("TLabel", background=C_BG, foreground=C_INK, font=UI_FONT)
    st.configure("Card.TLabel", background=C_CARD, foreground=C_INK, font=UI_FONT)
    st.configure("Hint.TLabel", background=C_CARD, foreground=C_INK2, font=("Segoe UI", 9))
    st.configure("Field.TLabel", background=C_CARD, foreground=C_INK2, font=UI_FONT_B)
    st.configure("Sub.TLabel", background=C_CARD, foreground=C_ACCENT, font=UI_FONT_B)
    st.configure("TEntry", fieldbackground="white", bordercolor=C_BORDER, padding=4)
    st.configure("TSpinbox", fieldbackground="white", padding=3)
    st.configure("TSeparator", background=C_BORDER)

    st.configure("TButton", font=UI_FONT, padding=(12, 7))
    st.configure("Ghost.TButton", font=UI_FONT, padding=(12, 7),
                 background="#e4e9f2", foreground=C_INK, bordercolor=C_BORDER)
    st.map("Ghost.TButton", background=[("active", "#d5dcea")])
    st.configure("Accent.TButton", font=UI_FONT_B, padding=(14, 8),
                 background=C_ACCENT, foreground="white", bordercolor=C_ACCENT)
    st.map("Accent.TButton", background=[("active", C_ACCENT_D), ("disabled", "#a9c3e6")])
    st.configure("Danger.TButton", font=UI_FONT_B, padding=(14, 8),
                 background=C_DANGER, foreground="white", bordercolor=C_DANGER)
    st.map("Danger.TButton", background=[("active", C_DANGER_D), ("disabled", "#e0b3b3")])
    st.configure("TProgressbar", background=C_ACCENT, troughcolor="#e4e9f2", bordercolor=C_BG)
