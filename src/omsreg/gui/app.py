#!/usr/bin/env python3
"""Единое графическое приложение платформы «Обработка реестров ОМС».

Вкладки, хранение настроек и весь поток запуск/проверка/подтверждение/журнал
строятся автоматически из реестра утилит (omsreg.gui.registry). Само приложение
ничего не знает о конкретных утилитах — только об их спецификациях.

Задачи выполняются в фоновом потоке; значения полей читаются в главном потоке
(Tkinter не потокобезопасен), в задачу передаются уже обычные строки/числа.

Запуск:  python -m omsreg      (или omsreg-gui после установки пакета)
"""

from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from omsreg.core import QueueLogHandler
from omsreg.gui import config as cfg
from omsreg.gui.log_panel import LogPanel
from omsreg.gui.registry import discover
from omsreg.gui.spec import ParamKind, RunContext, UtilitySpec
from omsreg.gui.theme import (
    APP_TITLE,
    C_ACCENT,
    C_BG,
    C_BORDER,
    C_CARD,
    C_HEADER,
    C_HEADER_SUB,
    C_INK2,
    C_TAB_IDLE,
    TITLE_FONT,
    UI_FONT,
    UI_FONT_B,
    build_styles,
)


class UtilityTab:
    """Состояние одной вкладки: спецификация, переменные полей и кнопка «Открыть результат»."""

    def __init__(self, spec: UtilitySpec):
        self.spec = spec
        self.vars: dict[str, tk.Variable] = {}
        self.open_btn: ttk.Button | None = None

    def make_var(self, p) -> tk.Variable:
        if p.kind is ParamKind.INT:
            return tk.IntVar(value=int(p.default or 0))
        if p.kind is ParamKind.BOOL:
            return tk.BooleanVar(value=bool(p.default))
        return tk.StringVar(value=str(p.default))


class App(tk.Tk):
    def __init__(self, specs: list[UtilitySpec] | None = None):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("980x740")
        self.minsize(820, 620)
        self.configure(bg=C_BG)

        self.specs = specs if specs is not None else discover()
        self.queue: queue.Queue = queue.Queue()
        self.log_handler = QueueLogHandler(self.queue)
        self.running = False
        self.run_buttons: list[ttk.Button] = []
        self.tabs: dict[str, UtilityTab] = {}
        self.last_open: Path | None = None

        build_styles(self)
        self._build_header()
        self._build_tabs()
        self.log = LogPanel(self)

        self.log.write("Готово к работе. Выберите вкладку, укажите папку и нажмите кнопку.")
        self._load_config()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------ оформление
    def _build_header(self) -> None:
        head = tk.Frame(self, bg=C_HEADER)
        head.pack(fill="x", side="top")
        tk.Label(head, text=APP_TITLE, bg=C_HEADER, fg="white",
                 font=TITLE_FONT).pack(anchor="w", padx=20, pady=(14, 0))
        tk.Label(head, text="удаление ошибочных талонов и статистика стационара по DBF-файлам",
                 bg=C_HEADER, fg=C_HEADER_SUB, font=("Segoe UI", 9)).pack(
            anchor="w", padx=20, pady=(0, 12))
        tk.Frame(self, bg=C_ACCENT, height=3).pack(fill="x", side="top")

    def _build_tabs(self) -> None:
        self.active_tab = 0
        self.tab_buttons: list[tk.Label] = []
        self.tab_pages: list[tk.Frame] = []
        self.tabbar = tk.Frame(self, bg=C_BG)
        self.tabbar.pack(fill="x", side="top", padx=12, pady=(12, 0))
        holder = tk.Frame(self, bg=C_CARD, highlightthickness=1, highlightbackground=C_BORDER)
        holder.pack(fill="x", side="top", padx=12, pady=(0, 6))

        for i, spec in enumerate(self.specs):
            b = tk.Label(self.tabbar, text=spec.title, font=UI_FONT_B, padx=22, pady=11,
                         bg=C_TAB_IDLE, fg=C_INK2, cursor="hand2")
            b.pack(side="left", padx=(0, 3))
            b.bind("<Button-1>", lambda e, idx=i: self._select_tab(idx))
            b.bind("<Enter>", lambda e, idx=i: self._hover_tab(idx, True))
            b.bind("<Leave>", lambda e, idx=i: self._hover_tab(idx, False))
            self.tab_buttons.append(b)

            page = tk.Frame(holder, bg=C_CARD)
            page.columnconfigure(1, weight=1)
            tab = UtilityTab(spec)
            self.tabs[spec.id] = tab
            self._build_page(page, tab)
            self.tab_pages.append(page)

        ttk.Button(self.tabbar, text="Сохранить настройки", style="Ghost.TButton",
                   command=self._save_config).pack(side="right", padx=(6, 0))
        if self.specs:
            self._select_tab(0)

    def _select_tab(self, idx: int) -> None:
        self.active_tab = idx
        for i, b in enumerate(self.tab_buttons):
            b.config(bg=C_CARD if i == idx else C_TAB_IDLE,
                     fg=C_ACCENT if i == idx else C_INK2)
        for pg in self.tab_pages:
            pg.pack_forget()
        self.tab_pages[idx].pack(fill="both", expand=True, padx=22, pady=20)

    def _hover_tab(self, idx: int, on: bool) -> None:
        if idx != self.active_tab:
            self.tab_buttons[idx].config(bg="#eaeff7" if on else C_TAB_IDLE)

    # ------------------------------------------------ построение вкладки из схемы
    def _build_page(self, page: tk.Frame, tab: UtilityTab) -> None:
        spec = tab.spec
        ttk.Label(page, style="Hint.TLabel", text=spec.description).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
        row = 1
        for p in spec.params:
            if not p.advanced:
                row = self._build_field(page, row, tab, p)

        advanced = [p for p in spec.params if p.advanced]
        if advanced:
            ttk.Separator(page, orient="horizontal").grid(
                row=row, column=0, columnspan=3, sticky="ew", pady=(20, 8))
            ttk.Label(page, text="Дополнительно (обычно не требуется)", style="Sub.TLabel").grid(
                row=row + 1, column=0, columnspan=3, sticky="w", pady=(0, 2))
            row += 2
            for p in [p for p in advanced if not p.group]:
                row = self._build_field(page, row, tab, p)
            grouped = [p for p in advanced if p.group]
            if grouped:
                row = self._build_group(page, row, tab, grouped)

        bar = tk.Frame(page, bg=C_CARD)
        bar.grid(row=row, column=0, columnspan=3, sticky="w", pady=(22, 2))
        for j, a in enumerate(spec.actions):
            btn = ttk.Button(bar, text=a.label, style=a.style,
                             command=lambda s=spec, ac=a: self._run(s, ac))
            btn.pack(side="left", padx=(0 if j == 0 else 10, 0))
            self.run_buttons.append(btn)
        tab.open_btn = ttk.Button(bar, text="Открыть результат", style="Ghost.TButton",
                                  state="disabled", command=self._open_last)
        tab.open_btn.pack(side="left", padx=(14, 0))

    def _build_field(self, page: tk.Frame, row: int, tab: UtilityTab, p) -> int:
        var = tab.make_var(p)
        tab.vars[p.key] = var
        pady = (10, 0)
        ttk.Label(page, text=p.label, style="Field.TLabel").grid(
            row=row, column=0, sticky="w", padx=(0, 12), pady=pady)

        if p.kind in (ParamKind.DIR, ParamKind.FILE, ParamKind.PATH):
            ttk.Entry(page, textvariable=var, font=UI_FONT).grid(
                row=row, column=1, sticky="ew", pady=pady)
            if p.kind is ParamKind.DIR:
                ttk.Button(page, text="Обзор…", style="Ghost.TButton",
                           command=lambda v=var: self._pick_dir(v)).grid(
                    row=row, column=2, sticky="w", padx=(8, 0), pady=pady)
            elif p.kind is ParamKind.FILE:
                ft = list(p.filetypes)
                ttk.Button(page, text="Обзор…", style="Ghost.TButton",
                           command=lambda v=var, f=ft: self._pick_file(v, f)).grid(
                    row=row, column=2, sticky="w", padx=(8, 0), pady=pady)
            else:  # PATH — файл или папка
                ft = list(p.filetypes)
                bf = tk.Frame(page, bg=C_CARD)
                bf.grid(row=row, column=2, sticky="w", padx=(8, 0), pady=pady)
                ttk.Button(bf, text="Файл…", style="Ghost.TButton",
                           command=lambda v=var, f=ft: self._pick_file(v, f)).pack(side="left")
                ttk.Button(bf, text="Папка…", style="Ghost.TButton",
                           command=lambda v=var: self._pick_dir(v)).pack(side="left", padx=(6, 0))
        elif p.kind is ParamKind.INT:
            ttk.Spinbox(page, from_=p.min, to=p.max, width=p.width or 6,
                        textvariable=var).grid(row=row, column=1, sticky="w", pady=pady)
        elif p.kind is ParamKind.BOOL:
            ttk.Checkbutton(page, variable=var).grid(row=row, column=1, sticky="w", pady=pady)
        else:  # TEXT
            entry = ttk.Entry(page, textvariable=var, font=UI_FONT,
                              **({"width": p.width} if p.width else {}))
            entry.grid(row=row, column=1, sticky="w" if p.width else "ew", pady=pady)

        row += 1
        if p.hint:
            ttk.Label(page, text=p.hint, style="Hint.TLabel").grid(
                row=row, column=1, columnspan=2, sticky="w")
            row += 1
        return row

    def _build_group(self, page: tk.Frame, row: int, tab: UtilityTab, params: list) -> int:
        """Группа коротких полей (напр. имена полей DBF) — сеткой по два в ряд."""
        ff = tk.Frame(page, bg=C_CARD)
        ff.grid(row=row, column=0, columnspan=3, sticky="w", pady=(6, 0))
        for i, p in enumerate(params):
            var = tab.make_var(p)
            tab.vars[p.key] = var
            r, c = divmod(i, 2)
            ttk.Label(ff, text=p.label, style="Field.TLabel").grid(
                row=r, column=c * 2, sticky="w", padx=(0, 8), pady=(6, 0))
            ttk.Entry(ff, textvariable=var, font=UI_FONT, width=p.width or 14).grid(
                row=r, column=c * 2 + 1, sticky="w", padx=(0, 22), pady=(6, 0))
        return row + 1

    # ------------------------------------------------ выбор файлов/папок
    def _pick_dir(self, var: tk.Variable) -> None:
        d = filedialog.askdirectory(title="Выберите папку", initialdir=self._initdir(var))
        if d:
            var.set(d)

    def _pick_file(self, var: tk.Variable, filetypes: list) -> None:
        f = filedialog.askopenfilename(title="Выберите файл", initialdir=self._initdir(var),
                                       filetypes=filetypes + [("Все файлы", "*.*")])
        if f:
            var.set(f)

    @staticmethod
    def _initdir(var: tk.Variable) -> str:
        cur = str(var.get()).strip()
        if cur:
            p = Path(cur)
            return str(p if p.is_dir() else p.parent)
        return os.getcwd()

    # ------------------------------------------------ запуск задач
    def _run(self, spec: UtilitySpec, action) -> None:
        if self.running:
            return
        tab = self.tabs[spec.id]
        params: dict = {}
        for p in spec.params:
            try:
                val = tab.vars[p.key].get()
            except tk.TclError:
                messagebox.showwarning(APP_TITLE, f"Некорректное значение поля: {p.label}")
                return
            if isinstance(val, str):
                val = val.strip()
            if p.required and val in ("", None):
                messagebox.showwarning(APP_TITLE, p.require_msg or f"Укажите: {p.label}")
                return
            params[p.key] = val

        if spec.validate:
            err = spec.validate(params)
            if err:
                messagebox.showwarning(APP_TITLE, err)
                return

        params.update(action.inject)

        if action.destructive:
            msg = (spec.confirm_message or self._default_confirm)(params)
            if not messagebox.askyesno(APP_TITLE, msg, icon="warning", default="no"):
                return

        ctx = RunContext(params, action, self.log_handler)
        self._start(spec.id, lambda: spec.run(ctx))

    @staticmethod
    def _default_confirm(params: dict) -> str:
        return ("Будут БЕЗВОЗВРАТНО удалены записи из DBF-файлов.\n"
                "Перед изменением каждого файла создаётся резервная копия (папка backup_…).\n\n"
                "Продолжить удаление?")

    def _start(self, util_id: str, func) -> None:
        self.running = True
        for b in self.run_buttons:
            b.config(state="disabled")
        self.log.start_progress()

        def work():
            result, exc = None, None
            try:
                result = func()
            except BaseException as e:  # JobError и любые прочие ошибки/прерывания
                exc = e
            finally:
                # "done" отправляется всегда — иначе интерфейс завис бы навсегда
                self.queue.put(("done", (util_id, result, exc)))

        threading.Thread(target=work, daemon=True).start()
        self.after(60, self._poll)

    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "log":
                    self.log.write(payload)
                elif kind == "done":
                    self._finish(*payload)
        except queue.Empty:
            pass
        if self.running:
            self.after(60, self._poll)

    def _finish(self, util_id: str, result, exc) -> None:
        self.running = False
        self.log.stop_progress()
        for b in self.run_buttons:
            b.config(state="normal")

        if exc is not None:
            self.log.write(f"ОШИБКА: {exc}", tag="err")
            messagebox.showerror(APP_TITLE, str(exc))
            return

        if result.log_text:
            self.log.write(result.log_text)
        self.log.write(result.summary, tag="err" if result.had_error else "ok")

        if result.open_path:
            self.last_open = Path(result.open_path)
            tab = self.tabs.get(util_id)
            if tab and tab.open_btn is not None:
                tab.open_btn.config(state="normal")
            self._open_last()

        box = {"info": messagebox.showinfo, "warning": messagebox.showwarning,
               "error": messagebox.showerror}.get(result.box_kind, messagebox.showinfo)
        box(APP_TITLE, result.summary)

    def _open_last(self) -> None:
        if self.last_open and Path(self.last_open).exists():
            try:
                webbrowser.open(Path(self.last_open).resolve().as_uri())
            except Exception as e:
                self.log.write(f"Не удалось открыть файл автоматически: {e}", tag="err")

    # ------------------------------------------------ конфиг (настройки.txt)
    def _iter_config(self):
        for spec in self.specs:
            tab = self.tabs[spec.id]
            for p in spec.params:
                if p.persist and p.key in tab.vars:
                    yield spec.id, p, tab.vars[p.key]

    def _load_config(self) -> None:
        path = cfg.config_path()
        if not path.exists():
            if self._save_config(silent=True):  # создаётся при первом старте
                self.log.write(f"Создан файл настроек: {path}")
            return
        try:
            data = cfg.read_kv(path)
        except OSError as e:
            self.log.write(f"Не удалось прочитать настройки: {e}", tag="err")
            return
        for util_id, p, var in self._iter_config():
            raw = data.get(p.config_key(util_id))
            if raw is None and p.legacy_key:
                raw = data.get(p.legacy_key)  # миграция со старой версии
            if raw is None:
                continue
            if isinstance(var, tk.IntVar):
                try:
                    var.set(int(raw))
                except ValueError:
                    pass
            else:
                var.set(raw)
        self.log.write(f"Настройки загружены из {path.name}")

    def _save_config(self, silent: bool = False) -> bool:
        path = cfg.config_path()
        items = []
        for util_id, p, var in self._iter_config():
            try:
                val = var.get()
            except tk.TclError:
                val = ""
            items.append((p.config_key(util_id), str(val)))
        try:
            cfg.write_kv(path, items)
            if not silent:
                self.log.write(f"Настройки сохранены в {path.name}", tag="ok")
            return True
        except OSError as e:
            if not silent:
                messagebox.showerror(APP_TITLE, f"Не удалось сохранить настройки:\n{e}")
            return False

    def _on_close(self) -> None:
        if self.running and not messagebox.askyesno(
                APP_TITLE, "Идёт обработка. Точно закрыть программу?", default="no"):
            return
        self._save_config(silent=True)
        self.destroy()


def main() -> None:
    App().mainloop()


if __name__ == "__main__":
    main()
