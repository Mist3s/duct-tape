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

import logging
import queue
import threading
import tkinter as tk
import webbrowser
from importlib.resources import as_file, files
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from omsreg.core import JobError, QueueLogHandler
from omsreg.gui import config as cfg
from omsreg.gui.log_panel import LogPanel, autohide_scrollbar
from omsreg.gui.registry import discover
from omsreg.gui.spec import BoxKind, ParamKind, RunContext, UtilitySpec
from omsreg.gui.theme import (
    APP_TITLE,
    C_ACCENT,
    C_BG,
    C_BORDER,
    C_CARD,
    C_HEADER,
    C_HEADER_SUB,
    C_INK,
    C_INK2,
    C_TAB_IDLE,
    MONO_FONT,
    TITLE_FONT,
    UI_FONT,
    UI_FONT_B,
    build_styles,
)

log = logging.getLogger(__name__)

# отступ рабочей области от края карточки по вертикали (pady страницы вкладки);
# высота карточки считается из него же, чтобы знание не разъезжалось по трём местам
PAGE_PADY = 20

# Прокрутка колесом: Windows/macOS присылают <MouseWheel> с полем delta,
# X11 — <Button-4>/<Button-5> с номером кнопки num.
WHEEL_SEQUENCES = ("<MouseWheel>", "<Button-4>", "<Button-5>")
X11_WHEEL_UP = 4

# вид итогового окна -> функция messagebox; перечислены ВСЕ значения BoxKind,
# поэтому опечатка в плагине невозможна (это проверено тестом)
MESSAGE_BOXES = {
    BoxKind.INFO: messagebox.showinfo,
    BoxKind.WARNING: messagebox.showwarning,
    BoxKind.ERROR: messagebox.showerror,
}


class TextAreaVar:
    """Обёртка над tk.Text с интерфейсом tk.Variable (get/set).

    Нужна, чтобы многострочное поле встраивалось в общий механизм чтения
    значений полей и сохранения настроек.
    """

    def __init__(self, text_widget):
        self._t = text_widget

    def get(self) -> str:
        """Весь текст поля без завершающего перевода строки."""
        return self._t.get("1.0", "end-1c")

    def set(self, value) -> None:
        """Заменяет содержимое поля строковым представлением value."""
        self._t.delete("1.0", "end")
        self._t.insert("1.0", str(value))


class UtilityTab:
    """Состояние одной вкладки: спецификация, переменные полей и кнопка «Открыть результат»."""

    def __init__(self, spec: UtilitySpec):
        self.spec = spec
        self.vars: dict[str, tk.Variable] = {}
        self.open_btn: ttk.Button | None = None

    def make_var(self, p) -> tk.Variable:
        """Переменная tkinter под вид параметра, заполненная значением по умолчанию."""
        if p.kind is ParamKind.INT:
            return tk.IntVar(value=int(p.default or 0))
        if p.kind is ParamKind.BOOL:
            return tk.BooleanVar(value=bool(p.default))
        return tk.StringVar(value=str(p.default))


class App(tk.Tk):
    """Главное окно платформы: вкладки утилит, журнал, настройки и запуск задач.

    Держит реестр спецификаций (self.specs), состояние вкладок (self.tabs),
    очередь строк журнала из рабочего потока (self.queue) и признак занятости
    (self.running). Задача выполняется в фоновом потоке, а весь обмен с
    интерфейсом идёт через очередь: Tkinter не потокобезопасен.
    """

    def __init__(self, specs: list[UtilitySpec] | None = None):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1120x880")
        self.minsize(1000, 720)
        self.configure(bg=C_BG)
        self._set_window_icon()

        self.specs = specs if specs is not None else discover()
        self.queue: queue.Queue = queue.Queue()
        self.log_handler = QueueLogHandler(self.queue)
        self.running = False
        self.run_buttons: list[ttk.Button] = []
        self.tabs: dict[str, UtilityTab] = {}
        self.last_open: Path | None = None

        build_styles(self)
        self._build_header()
        self._build_body()
        self.log = LogPanel(self)

        self.log.write("Готово к работе. Выберите утилиту слева, укажите папку и нажмите кнопку.")
        self._load_config()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------ оформление
    def _set_window_icon(self) -> None:
        """Иконка окна (рулон синей изоленты). Пропускается, если файл недоступен."""
        try:
            with as_file(files("omsreg.gui").joinpath("assets/icon.png")) as path:
                self._icon_img = tk.PhotoImage(file=str(path))
            self.iconphoto(True, self._icon_img)
        except Exception as e:  # оформление не повод не запускать программу
            log.debug("Иконка окна не загружена: %s", e)

    def _build_header(self) -> None:
        head = tk.Frame(self, bg=C_HEADER)
        head.pack(fill="x", side="top")
        tk.Label(head, text=APP_TITLE, bg=C_HEADER, fg="white",
                 font=TITLE_FONT).pack(anchor="w", padx=20, pady=(14, 0))
        tk.Label(head, text="удаление ошибочных талонов и статистика стационара по DBF-файлам",
                 bg=C_HEADER, fg=C_HEADER_SUB, font=("Segoe UI", 9)).pack(
            anchor="w", padx=20, pady=(0, 12))
        tk.Frame(self, bg=C_ACCENT, height=3).pack(fill="x", side="top")

    def _build_body(self) -> None:
        """Строит рабочую область: список утилит слева, поля выбранной утилиты справа.

        Список вертикальный и с прокруткой, если не влезает по высоте. Такая
        раскладка масштабируется на любое число утилит: горизонтальная полоса
        вкладок ломалась уже на 4-5 длинных названиях.
        """
        sidebar_w = 232
        self.active_tab = 0
        self.tab_buttons: list[tk.Label] = []
        self.tab_pages: list[tk.Frame] = []

        mid = tk.Frame(self, bg=C_BG)
        mid.pack(side="top", fill="x", padx=12, pady=(12, 6))

        # --- боковой список утилит в прокручиваемом холсте ---
        side = tk.Frame(mid, bg=C_BG)
        side.pack(side="left", fill="y")
        canvas = tk.Canvas(side, bg=C_BG, width=sidebar_w, highlightthickness=0, bd=0)
        vsb = ttk.Scrollbar(side, orient="vertical", command=canvas.yview)
        canvas.pack(side="left", fill="y")
        inner = tk.Frame(canvas, bg=C_BG)
        canvas.create_window((0, 0), window=inner, anchor="nw", width=sidebar_w)

        canvas.configure(yscrollcommand=autohide_scrollbar(vsb, "left"))
        inner.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))

        def _wheel(e) -> None:
            if inner.winfo_reqheight() <= canvas.winfo_height():
                return  # список влезает целиком — прокручивать нечего
            up = getattr(e, "delta", 0) > 0 or getattr(e, "num", 0) == X11_WHEEL_UP
            canvas.yview_scroll(-1 if up else 1, "units")

        def _grab_wheel(_e) -> None:
            for seq in WHEEL_SEQUENCES:
                canvas.bind_all(seq, _wheel)

        def _release_wheel(_e) -> None:
            for seq in WHEEL_SEQUENCES:
                canvas.unbind_all(seq)

        canvas.bind("<Enter>", _grab_wheel)
        canvas.bind("<Leave>", _release_wheel)

        # --- панель полей выбранной утилиты ---
        self.content_holder = tk.Frame(mid, bg=C_CARD, highlightthickness=1,
                                       highlightbackground=C_BORDER)
        self.content_holder.pack(side="left", fill="x", expand=True, padx=(10, 0))

        for i, spec in enumerate(self.specs):
            b = tk.Label(inner, text=spec.title, font=UI_FONT_B, anchor="w", justify="left",
                         padx=16, pady=12, bg=C_TAB_IDLE, fg=C_INK2, cursor="hand2",
                         wraplength=sidebar_w - 32)
            b.pack(fill="x", pady=(0, 2))
            b.bind("<Button-1>", lambda _e, idx=i: self._select_tab(idx))
            b.bind("<Enter>", lambda _e, idx=i: self._hover_tab(idx, True))
            b.bind("<Leave>", lambda _e, idx=i: self._hover_tab(idx, False))
            self.tab_buttons.append(b)

            page = tk.Frame(self.content_holder, bg=C_CARD)
            page.columnconfigure(1, weight=1)
            tab = UtilityTab(spec)
            self.tabs[spec.id] = tab
            self._build_page(page, tab)
            self.tab_pages.append(page)

        # Фиксируем высоту рабочей области по самой высокой утилите — иначе при
        # переключении журнал «прыгает». Ту же высоту получает боковой холст,
        # поэтому полоса прокрутки меню не мелькает при старте.
        page_h = 0
        for pg in self.tab_pages:
            pg.pack(fill="both", expand=True, padx=22, pady=PAGE_PADY)
            self.update_idletasks()
            page_h = max(page_h, pg.winfo_reqheight())
            pg.pack_forget()
        holder_h = page_h + 2 * PAGE_PADY  # отступы страницы сверху и снизу
        self.content_holder.configure(height=holder_h)
        self.content_holder.pack_propagate(False)
        canvas.configure(height=holder_h)

        if self.specs:
            self._select_tab(0)
        self.update_idletasks()
        canvas.configure(scrollregion=canvas.bbox("all"))
        canvas.yview_moveto(0)  # пересчитать видимость полосы прокрутки после раскладки

    def _select_tab(self, idx: int) -> None:
        self.active_tab = idx
        for i, b in enumerate(self.tab_buttons):
            b.config(bg=C_CARD if i == idx else C_TAB_IDLE,
                     fg=C_ACCENT if i == idx else C_INK2)
        for pg in self.tab_pages:
            pg.pack_forget()
        self.tab_pages[idx].pack(fill="both", expand=True, padx=22, pady=PAGE_PADY)

    def _hover_tab(self, idx: int, on: bool) -> None:
        if idx != self.active_tab:
            self.tab_buttons[idx].config(bg="#eaeff7" if on else C_TAB_IDLE)

    # ------------------------------------------------ построение вкладки из схемы
    def _build_page(self, page: tk.Frame, tab: UtilityTab) -> None:
        spec = tab.spec
        # единая ширина описания для всех утилит; текст переносится сам
        ttk.Label(page, style="Hint.TLabel", text=spec.description, wraplength=650,
                  justify="left").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
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

        # распорка забирает свободную высоту и прижимает ряд кнопок к низу рабочей области
        tk.Frame(page, bg=C_CARD, height=0).grid(row=row, column=0)
        page.rowconfigure(row, weight=1)
        row += 1

        bar = tk.Frame(page, bg=C_CARD)
        bar.grid(row=row, column=0, columnspan=3, sticky="swe", pady=(16, 0))
        for j, a in enumerate(spec.actions):
            btn = ttk.Button(bar, text=a.label, style=a.style,
                             command=lambda s=spec, ac=a: self._run(s, ac))
            btn.pack(side="left", padx=(0 if j == 0 else 10, 0))
            self.run_buttons.append(btn)
        tab.open_btn = ttk.Button(bar, text="Открыть результат", style="Ghost.TButton",
                                  state="disabled", command=self._open_last)
        tab.open_btn.pack(side="left", padx=(14, 0))
        # глобальная кнопка сохранения настроек — прижата к правому краю ряда
        ttk.Button(bar, text="Сохранить настройки", style="Ghost.TButton",
                   command=self._save_config).pack(side="right")

    def _build_field(self, page: tk.Frame, row: int, tab: UtilityTab, p) -> int:
        if p.kind is ParamKind.TEXTAREA:
            return self._build_textarea(page, row, tab, p)
        var = tab.make_var(p)
        tab.vars[p.key] = var
        pady = (10, 0)
        # у флажка подпись на самом Checkbutton и он тянется на всю строку — иначе длинная
        # подпись в колонке 0 расширяет её и перекашивает все поля вкладки
        if p.kind is not ParamKind.BOOL:
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
            ttk.Checkbutton(page, text=p.label, variable=var).grid(
                row=row, column=0, columnspan=3, sticky="w", pady=pady)
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

    def _build_textarea(self, page: tk.Frame, row: int, tab: UtilityTab, p) -> int:
        """Многострочное поле ввода (tk.Text с прокруткой) — напр. вставка списка кодов."""
        # без подписи (пустой label) поле растягивается на всю ширину рабочей области
        if p.label:
            ttk.Label(page, text=p.label, style="Field.TLabel").grid(
                row=row, column=0, sticky="nw", padx=(0, 12), pady=(12, 0))
            box_col, box_span = 1, 2
        else:
            box_col, box_span = 0, 3
        box = tk.Frame(page, bg=C_CARD, highlightthickness=1, highlightbackground=C_BORDER)
        box.grid(row=row, column=box_col, columnspan=box_span, sticky="ew", pady=(12, 0))
        sb = ttk.Scrollbar(box)
        txt = tk.Text(box, height=p.height or 6, wrap="word", font=MONO_FONT, relief="flat",
                      bg="white", fg=C_INK, insertbackground=C_INK, undo=True, padx=8, pady=6)

        txt.configure(yscrollcommand=autohide_scrollbar(sb, "right", before=txt))
        sb.config(command=txt.yview)
        txt.pack(side="left", fill="both", expand=True)
        if p.default:
            txt.insert("1.0", str(p.default))
        tab.vars[p.key] = TextAreaVar(txt)
        row += 1
        if p.hint:
            ttk.Label(page, text=p.hint, style="Hint.TLabel").grid(
                row=row, column=box_col, columnspan=box_span, sticky="w")
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
        d = filedialog.askdirectory(title="Выберите папку", initialdir=self._init_dir(var))
        if d:
            var.set(d)

    def _pick_file(self, var: tk.Variable, filetypes: list) -> None:
        f = filedialog.askopenfilename(title="Выберите файл", initialdir=self._init_dir(var),
                                       filetypes=filetypes + [("Все файлы", "*.*")])
        if f:
            var.set(f)

    @staticmethod
    def _init_dir(var: tk.Variable) -> str:
        cur = str(var.get()).strip()
        if cur:
            p = Path(cur)
            return str(p if p.is_dir() else p.parent)
        return str(Path.cwd())

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
        """Текст подтверждения разрушающего действия, если плагин не задал свой.

        Единственная формулировка предупреждения об удалении на всю платформу:
        если у утилиты есть параметр «dir», в текст добавляется сама папка.
        """
        target = params.get("dir")
        head = (f"Будут БЕЗВОЗВРАТНО удалены записи из DBF-файлов в папке:\n{target}" if target
                else "Будут БЕЗВОЗВРАТНО удалены записи из DBF-файлов.")
        return (f"{head}\n\n"
                "Перед изменением каждого файла создаётся резервная копия (папка backup_…).\n\n"
                "Продолжить удаление?")

    def _job_logger(self) -> logging.Logger:
        """Логгер запущенной задачи — тот, к которому утилита прицепила наш обработчик.

        Только через него запись попадает и в панель журнала, и в .log-файл задачи.
        Если задача не успела настроить логирование, остаётся логгер интерфейса.
        """
        for name in list(logging.Logger.manager.loggerDict):
            candidate = logging.getLogger(name)
            if self.log_handler in getattr(candidate, "handlers", ()):
                return candidate
        if self.log_handler not in log.handlers:
            log.addHandler(self.log_handler)
            log.setLevel(logging.INFO)
        return log

    def _start(self, util_id: str, func) -> None:
        self.running = True
        for b in self.run_buttons:
            b.config(state="disabled")
        self.log.start_progress()

        def work():
            result, exc = None, None
            try:
                result = func()
            except JobError as e:
                exc = e  # ожидаемая ошибка: её текст написан для пользователя
            except Exception as e:
                # внутренняя ошибка утилиты: трассировка нужна в журнале задачи и в .log,
                # иначе разбирать инцидент нечем — пользователю видна одна строка
                self._job_logger().exception("Внутренняя ошибка при выполнении задачи")
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

        if exc is not None or result is None:
            # трассировка внутренней ошибки уже в журнале (см. _start), пользователю —
            # только короткая строка: текст JobError для него и написан, остальное — нет
            if isinstance(exc, JobError):
                msg = str(exc)
            elif exc is not None:
                msg = "Внутренняя ошибка, подробности в журнале."
            else:  # задачу прервали (SystemExit/KeyboardInterrupt) — результата нет
                msg = "Задача прервана и не вернула результат."
            self.log.write(f"ОШИБКА: {msg}", tag="err")
            messagebox.showerror(APP_TITLE, msg)
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

        # .get с запасным вариантом: сторонняя вкладка может вернуть box_kind строкой,
        # и из-за этого пользователь не должен остаться без итогового окна
        MESSAGE_BOXES.get(result.box_kind, messagebox.showinfo)(APP_TITLE, result.summary)

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
            data, bad_lines = cfg.read_kv(path)
        except OSError as e:
            self.log.write(f"Не удалось прочитать настройки: {e}", tag="err")
            return
        for line in bad_lines:  # файл правят вручную — опечатка не должна исчезать
            self.log.write(f"Настройки, строка не понята (нет «=»), пропущена — {line}", tag="warn")
        bad_numbers = []
        for util_id, p, var in self._iter_config():
            raw = data.get(p.config_key(util_id))
            if raw is None and p.legacy_key:
                raw = data.get(p.legacy_key)  # миграция со старой версии
            if raw is None:
                continue
            if p.kind is ParamKind.INT:  # схема поля, а не тип переменной Tkinter
                try:
                    var.set(int(raw))
                except ValueError:
                    # остаётся значение по умолчанию, но не молча: для длины кода
                    # от него зависит, какие коды попадут под удаление
                    bad_numbers.append(f"{p.config_key(util_id)} = {raw} (нужно число, "
                                       f"взято {var.get()})")
            else:
                var.set(raw)
        for item in bad_numbers:
            self.log.write(f"Настройки, значение не число — {item}", tag="warn")
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
            # и в тихом режиме сбой записи виден: иначе настройки сеанса теряются молча
            self.log.write(f"Не удалось сохранить настройки в {path}: {e}", tag="err")
            if not silent:
                messagebox.showerror(APP_TITLE, f"Не удалось сохранить настройки:\n{e}")
            return False

    def _on_close(self) -> None:
        if self.running and not messagebox.askyesno(
                APP_TITLE, "Идёт обработка. Точно закрыть программу?", default="no"):
            return
        if not self._save_config(silent=True):
            messagebox.showwarning(APP_TITLE, "Настройки этого сеанса не сохранены: "
                                              "не удалось записать файл настроек.")
        self.destroy()


def main() -> None:
    """Точка входа графического интерфейса."""
    App().mainloop()


if __name__ == "__main__":
    main()
