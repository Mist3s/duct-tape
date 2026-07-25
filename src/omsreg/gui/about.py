"""Страница «О программе»: версия, автор, ссылка на GitHub и обновление.

Отдельная страница бокового списка (не утилита: ни параметров, ни запуска задачи).
Здесь же живёт вся работа с обновлениями со стороны интерфейса — проверка в фоновом
потоке, статус вместо всплывающих ошибок и подтверждение установки. Сама механика
(запрос к GitHub, sha256, подмена файла) — в omsreg.core.updater.

Ошибки проверки НИКОГДА не показываются модальным окном при автопроверке: программа
работает с медицинскими данными в сетях, где GitHub может быть закрыт, и мешать
работе из-за этого нельзя. При ручной проверке текст ошибки виден на самой странице.
"""

from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

from omsreg import __version__
from omsreg.core import updater
from omsreg.gui.theme import (
    APP_TITLE,
    C_ACCENT,
    C_CARD,
    C_INK,
    C_INK2,
    UI_FONT,
    UI_FONT_B,
)

log = logging.getLogger("omsreg.gui.about")

AUTHOR = "Ivanov Andrey"
LICENSE = "MIT"
_MB = 1024 * 1024


class AboutPage:
    """Содержимое страницы «О программе» и вся логика проверки/установки обновления."""

    def __init__(self, parent: tk.Frame, check_var: tk.BooleanVar,
                 log_write=None, on_update_found=None):
        """Собирает страницу внутри parent.

        check_var — галочка «Проверять обновления при запуске» (значение хранится
        в настройках); log_write(text, tag) — необязательная запись в журнал программы;
        on_update_found(версия) — необязательное уведомление оболочки, чтобы она могла
        отметить пункт бокового списка (пользователь может смотреть на другую вкладку).
        """
        self.parent = parent
        self.check_var = check_var
        self._log_write = log_write
        self._on_update_found = on_update_found
        self._busy = False
        self._release = None
        # Обмен с рабочим потоком — через очередь, а не widget.after() из потока:
        # Tkinter не потокобезопасен, и общий опрос приложения (_poll) работает только
        # пока выполняется задача утилиты, поэтому у страницы свой опрос (_pump).
        self._ui: queue.Queue = queue.Queue()
        self._build()

    # ------------------------------------------------ раскладка
    def _build(self) -> None:
        # Название и подзаголовок программы не повторяются: они постоянно видны в шапке
        # окна над этой страницей — здесь только то, чего там нет.
        p = self.parent
        self._row(0, "Версия:", f"{__version__}")
        self._row(1, "Автор:", AUTHOR)
        self._row(2, "Лицензия:", LICENSE)

        tk.Label(p, text="GitHub:", bg=C_CARD, fg=C_INK2, font=UI_FONT_B).grid(
            row=3, column=0, sticky="w", pady=(0, 2))
        link = tk.Label(p, text=f"github.com/{updater.REPO}", bg=C_CARD, fg=C_ACCENT,
                        font=UI_FONT, cursor="hand2")
        link.grid(row=3, column=1, sticky="w", pady=(0, 2))
        link.bind("<Button-1>", lambda _e: self._open(f"https://github.com/{updater.REPO}"))

        ttk.Separator(p, orient="horizontal").grid(
            row=4, column=0, columnspan=2, sticky="ew", pady=14)

        tk.Label(p, text="Обновление", bg=C_CARD, fg=C_INK, font=UI_FONT_B).grid(
            row=5, column=0, columnspan=2, sticky="w")

        bar = tk.Frame(p, bg=C_CARD)
        bar.grid(row=6, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.check_btn = ttk.Button(bar, text="Проверить обновление",
                                    style="Accent.TButton", command=self.check_manual)
        self.check_btn.pack(side="left")
        self.install_btn = ttk.Button(bar, text="Обновить", style="Accent.TButton",
                                      command=self.install, state="disabled")
        self.install_btn.pack(side="left", padx=(10, 0))
        self.page_btn = ttk.Button(bar, text="Страница релиза", style="Ghost.TButton",
                                   command=lambda: self._open(updater.RELEASES_PAGE))
        self.page_btn.pack(side="left", padx=(10, 0))

        self.status = tk.Label(p, text="", bg=C_CARD, fg=C_INK2, font=UI_FONT,
                               justify="left", wraplength=560, anchor="w")
        self.status.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(10, 0))

        ttk.Checkbutton(p, text="Проверять обновления при запуске программы",
                        variable=self.check_var).grid(
            row=8, column=0, columnspan=2, sticky="w", pady=(14, 0))

    def _row(self, row: int, label: str, value: str) -> None:
        tk.Label(self.parent, text=label, bg=C_CARD, fg=C_INK2, font=UI_FONT_B).grid(
            row=row, column=0, sticky="w", pady=(0, 2))
        tk.Label(self.parent, text=value, bg=C_CARD, fg=C_INK, font=UI_FONT).grid(
            row=row, column=1, sticky="w", pady=(0, 2))

    # ------------------------------------------------ статус
    def set_status(self, text: str, kind: str = "info") -> None:
        """Пишет статус на странице. kind: info | good | warn."""
        colors = {"info": C_INK2, "good": "#2e7d32", "warn": "#b26a00"}
        self.status.config(text=text, fg=colors.get(kind, C_INK2))

    def _open(self, url: str) -> None:
        try:
            webbrowser.open(url)
        except OSError as e:  # нет браузера — не повод показывать ошибку модально
            self.set_status(f"Не удалось открыть ссылку: {e}", "warn")

    # ------------------------------------------------ проверка
    def check_manual(self) -> None:
        """Проверка по кнопке: ошибку показываем текстом на странице."""
        self._check(silent=False)

    def check_on_start(self) -> None:
        """Автопроверка при запуске: при ошибке только статус и запись в журнал."""
        if self.check_var.get():
            self._check(silent=True)
        else:
            self.set_status(f"Установлена версия {__version__}. "
                            "Автопроверка обновлений выключена.")

    def _start(self, work) -> None:
        """Запускает работу в фоновом потоке и включает опрос очереди в главном."""
        self._busy = True
        threading.Thread(target=work, daemon=True).start()
        self.parent.after(100, self._pump)

    def _pump(self) -> None:
        """Выполняет в ГЛАВНОМ потоке всё, что прислал рабочий (см. _ui)."""
        try:
            while True:
                method, args = self._ui.get_nowait()
                method(*args)
        except queue.Empty:
            pass
        if self._busy:
            self.parent.after(100, self._pump)

    def _post(self, method, *args) -> None:
        """Просит главный поток вызвать method(*args). Зовётся из рабочего потока."""
        self._ui.put((method, args))

    def _check(self, silent: bool) -> None:
        if self._busy:
            return
        self.check_btn.config(state="disabled")
        self.set_status("Проверяю наличие обновления…")

        def work() -> None:
            try:
                result = updater.check_latest(__version__)
            except updater.UpdateError as e:
                self._post(self._check_failed, str(e), silent)
            except Exception as e:  # неожиданное не должно ронять программу
                log.debug("Проверка обновления сорвалась: %s", e, exc_info=True)
                self._post(self._check_failed, f"Не удалось проверить обновление: {e}", silent)
            else:
                self._post(self._check_done, result)

        self._start(work)

    def _check_done(self, result) -> None:
        self._busy = False
        self.check_btn.config(state="normal")
        self._release = result.release
        if result.update_available:
            self.install_btn.config(state="normal")
            self.set_status(result.message + " Нажмите «Обновить», чтобы установить.", "good")
            self._journal(f"Доступно обновление: версия {result.latest}")
            if self._on_update_found:
                self._on_update_found(result.latest)
        else:
            self.install_btn.config(state="disabled")
            self.set_status(result.message, "info")

    def _check_failed(self, text: str, silent: bool) -> None:
        self._busy = False
        self.check_btn.config(state="normal")
        # текст ошибки виден на странице в любом случае; модальных окон не показываем
        self.set_status(f"Проверить обновление не удалось. {text}", "warn")
        if silent:
            log.debug("Автопроверка обновления: %s", text)
        else:
            self._journal(f"Проверка обновления: {text}", tag="warn")

    def _journal(self, text: str, tag: str = "") -> None:
        if self._log_write:
            self._log_write(text, tag)

    # ------------------------------------------------ установка
    def install(self) -> None:
        """Скачивает и ставит обновление после подтверждения, затем перезапускает программу."""
        release = self._release
        if self._busy or release is None:
            return
        if not updater.is_frozen():
            self.set_status("Программа запущена из исходников — обновите её через git pull.",
                            "warn")
            return
        size = f" ({release.size / _MB:.1f} МБ)" if release.size else ""
        if not messagebox.askyesno(
            APP_TITLE,
            f"Установить версию {release.version}{size}?\n\n"
            "Программа скачает обновление, заменит себя и запустится заново. "
            "Несохранённые настройки будут сохранены.",
            default="no",
        ):
            return

        self.install_btn.config(state="disabled")
        self.check_btn.config(state="disabled")

        def work() -> None:
            try:
                updater.update(release, progress=self._progress)
            except updater.UpdateError as e:
                self._post(self._install_failed, str(e))
            except Exception as e:
                log.debug("Установка обновления сорвалась: %s", e, exc_info=True)
                self._post(self._install_failed, f"Неожиданная ошибка: {e}")
            else:
                self._post(self._install_done)

        self._start(work)

    def _progress(self, got: int, total: int) -> None:
        """Показывает ход скачивания. Зовётся из рабочего потока — только через очередь."""
        text = (f"Скачано {got / _MB:.1f} из {total / _MB:.1f} МБ…" if total
                else f"Скачано {got / _MB:.1f} МБ…")
        self._post(self.set_status, text)

    def _install_done(self) -> None:
        self.set_status("Обновление установлено, программа перезапускается…", "good")
        self._journal("Обновление установлено, программа перезапускается")
        self.parent.after(400, self._exit_for_restart)

    def _exit_for_restart(self) -> None:
        """Закрывает программу так же, как крестик — чтобы настройки сохранились."""
        root = self.parent.winfo_toplevel()
        closer = getattr(root, "close_for_update", None)
        if callable(closer):
            closer()
        else:
            root.destroy()

    def _install_failed(self, text: str) -> None:
        self._busy = False
        self.check_btn.config(state="normal")
        self.install_btn.config(state="normal")
        self.set_status(f"Обновление не установлено. {text}", "warn")
        self._journal(f"Обновление не установлено: {text}", tag="err")
