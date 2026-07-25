"""Проверка и установка обновлений программы через релизы GitHub.

Всё на стандартной библиотеке: запрос к api.github.com (urllib), разбор JSON,
проверка sha256 и подмена файла программы. Модуль не импортирует tkinter, поэтому
тестируется без дисплея; сообщения об ошибках сразу человеческие — интерфейсу
достаточно показать текст UpdateError, ничего не переводя.

Как устроена установка (Windows не даёт перезаписать запущенный .exe, но
переименовать разрешает):
  1. скачиваем новый файл рядом с текущим как «duct-tape.new.exe»;
  2. сверяем sha256 с тем, что опубликован в API релиза — иначе не ставим;
  3. переименовываем текущий в «duct-tape.old.exe», новый — на его место;
  4. запускаем новый файл и выходим; при следующем старте cleanup_leftovers()
     удаляет «*.old.exe».
Если что-то падает на шаге 3-4, старый файл возвращается на место (откат).
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, NamedTuple

log = logging.getLogger("omsreg.core.updater")

REPO = "Mist3s/duct-tape"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
ASSET_NAME = "duct-tape.exe"

# короткий таймаут: проверка не должна задерживать запуск программы
CHECK_TIMEOUT = 6
DOWNLOAD_TIMEOUT = 120
_USER_AGENT = f"duct-tape-updater ({RELEASES_PAGE})"
_CHUNK = 64 * 1024


class UpdateError(Exception):
    """Ошибка проверки или установки обновления с готовым текстом для пользователя."""


class Release(NamedTuple):
    """Опубликованный релиз: версия, файл программы и его контрольная сумма."""

    version: str          # «1.5.0» — тег без ведущей «v»
    tag: str              # «v1.5.0» — как в GitHub
    url: str              # прямая ссылка на файл программы
    size: int             # размер файла, байт (0 — размер неизвестен)
    sha256: str           # ожидаемая контрольная сумма файла ('' — не опубликована)
    notes: str            # описание релиза (текст из GitHub)
    page: str             # страница релиза для браузера


class CheckResult(NamedTuple):
    """Итог проверки: что установлено, что опубликовано и нужно ли обновляться."""

    current: str                # установленная версия
    latest: str                 # опубликованная версия ('' — узнать не удалось)
    update_available: bool      # опубликованная версия новее установленной
    release: Release | None  # сведения о релизе (None, если обновление не нужно)

    @property
    def message(self) -> str:
        """Короткий человеческий статус для страницы «О программе»."""
        if self.update_available and self.release:
            return f"Доступна версия {self.latest} (установлена {self.current})."
        if self.latest and _parse_version(self.current) > _parse_version(self.latest):
            return f"Установлена версия {self.current} — новее опубликованной {self.latest}."
        return f"Установлена последняя версия ({self.current})."


# ----------------------------- версии -----------------------------

def _parse_version(text: str) -> tuple:
    """Версия как сравнимый ключ: «v1.5.0» -> ((1, 5, 0), 1).

    Второй элемент отделяет предвыпуск от релиза: у «1.5.0-rc1» он 0, у «1.5.0» — 1,
    поэтому обычный релиз считается новее своего release candidate. Нечисловой хвост
    в расчёт не берётся, неразбираемая строка даёт ((0,), 0) и никогда не выглядит новее.
    """
    raw = (text or "").strip().lstrip("vV")
    core, _, suffix = raw.partition("-")
    nums = []
    for part in core.split("."):
        digits = ""
        for ch in part:
            if not ch.isdigit():
                break
            digits += ch
        if not digits:
            break
        nums.append(int(digits))
    return (tuple(nums) or (0,), 0 if suffix else 1)


def is_newer(candidate: str, installed: str) -> bool:
    """True, если версия candidate строго новее installed."""
    return _parse_version(candidate) > _parse_version(installed)


# ----------------------------- проверка -----------------------------

def _fetch_json(url: str, timeout: int) -> dict:
    """GET с заголовками GitHub API. Сетевые сбои -> UpdateError с понятным текстом."""
    # адрес задан константами модуля, схема всегда https
    request = urllib.request.Request(
        url,
        headers={"User-Agent": _USER_AGENT, "Accept": "application/vnd.github+json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise UpdateError("На GitHub пока нет ни одного релиза.") from e
        if e.code in (403, 429):
            raise UpdateError("GitHub временно ограничил число запросов — "
                              "попробуйте позже.") from e
        raise UpdateError(f"GitHub ответил ошибкой {e.code}.") from e
    except urllib.error.URLError as e:
        raise UpdateError(f"Нет связи с GitHub: {e.reason}.") from e
    except TimeoutError as e:
        raise UpdateError("GitHub не ответил за отведённое время.") from e
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise UpdateError("GitHub вернул неожиданный ответ.") from e
    except OSError as e:  # обрыв TLS, отказ DNS и прочее — не роняем программу
        raise UpdateError(f"Не удалось обратиться к GitHub: {e}.") from e


def _asset(data: dict) -> tuple[str, int, str]:
    """(ссылка, размер, sha256) файла программы из описания релиза."""
    for item in data.get("assets") or ():
        if item.get("name") == ASSET_NAME:
            digest = str(item.get("digest") or "")
            sha = digest.split("sha256:", 1)[1].strip() if "sha256:" in digest else ""
            return str(item.get("browser_download_url") or ""), int(item.get("size") or 0), sha
    return "", 0, ""


def check_latest(current: str, timeout: int = CHECK_TIMEOUT) -> CheckResult:
    """Спрашивает у GitHub последний релиз и сравнивает с установленной версией.

    Ошибки сети и разбора поднимаются как UpdateError — вызывающий решает, показывать
    их (ручная проверка) или тихо записать в журнал (проверка при запуске).
    """
    data = _fetch_json(API_LATEST, timeout)
    tag = str(data.get("tag_name") or "").strip()
    if not tag:
        raise UpdateError("В ответе GitHub нет номера версии.")
    latest = tag.lstrip("vV")
    if not is_newer(latest, current):
        return CheckResult(current, latest, False, None)

    url, size, sha = _asset(data)
    release = Release(
        version=latest, tag=tag, url=url, size=size, sha256=sha,
        notes=str(data.get("body") or "").strip(),
        page=str(data.get("html_url") or RELEASES_PAGE),
    )
    return CheckResult(current, latest, True, release)


# ----------------------------- установка -----------------------------

def is_frozen() -> bool:
    """True, если программа собрана в .exe (PyInstaller), а не запущена из исходников."""
    return bool(getattr(sys, "frozen", False))


def current_exe() -> Path:
    """Путь к запущенному файлу программы."""
    return Path(sys.executable).resolve()


def _paths() -> tuple[Path, Path, Path]:
    """(текущий файл, куда скачиваем, куда отодвигаем прежний)."""
    exe = current_exe()
    return (exe,
            exe.with_name(f"{exe.stem}.new{exe.suffix}"),
            exe.with_name(f"{exe.stem}.old{exe.suffix}"))


def cleanup_leftovers() -> None:
    """Удаляет «*.old.exe», оставшийся от прошлого обновления. Тихая, вызывается при старте."""
    if not is_frozen():
        return
    _, _, old = _paths()
    try:
        old.unlink(missing_ok=True)
    except OSError as e:  # файл ещё занят прежним процессом — попробуем в другой раз
        log.debug("Не удалось удалить %s: %s", old.name, e)


def download(release: Release, dest: Path,
             progress: Callable[[int, int], None] | None = None,
             timeout: int = DOWNLOAD_TIMEOUT) -> Path:
    """Скачивает файл релиза в dest и проверяет sha256.

    progress(получено, всего) вызывается по мере скачивания (всего = 0, если размер
    неизвестен). Несовпадение контрольной суммы -> UpdateError, файл удаляется:
    подменённый или недокачанный файл не должен остаться на диске.
    """
    if not release.url:
        raise UpdateError(f"В релизе {release.tag} нет файла {ASSET_NAME}.")
    # ссылка получена из ответа GitHub API по https
    request = urllib.request.Request(release.url, headers={"User-Agent": _USER_AGENT})
    digest = hashlib.sha256()
    got = 0
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            total = int(response.headers.get("Content-Length") or release.size or 0)
            with dest.open("wb") as out:
                while True:
                    chunk = response.read(_CHUNK)
                    if not chunk:
                        break
                    out.write(chunk)
                    digest.update(chunk)
                    got += len(chunk)
                    if progress:
                        progress(got, total)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        dest.unlink(missing_ok=True)
        raise UpdateError(f"Не удалось скачать обновление: {e}.") from e

    if release.sha256 and digest.hexdigest() != release.sha256.lower():
        dest.unlink(missing_ok=True)
        raise UpdateError("Контрольная сумма скачанного файла не совпала — "
                          "обновление отменено.")
    return dest


def install_and_restart(new_file: Path) -> None:
    """Ставит скачанный файл на место текущего и запускает обновлённую программу.

    Вызывающий обязан после этого завершить программу: пока прежний процесс жив,
    файл «*.old.exe» удалить нельзя (это делает следующий запуск).
    При сбое подмены прежний файл возвращается на место, и поднимается UpdateError.
    """
    if not is_frozen():
        raise UpdateError("Программа запущена из исходников — обновите её через git pull.")
    exe, _, old = _paths()
    try:
        old.unlink(missing_ok=True)
        exe.replace(old)          # переименовать запущенный .exe Windows разрешает
    except OSError as e:
        raise UpdateError(f"Нет прав заменить {exe.name}: {e}. "
                          "Запустите программу от имени администратора или "
                          "замените файл вручную.") from e
    try:
        new_file.replace(exe)
    except OSError as e:
        old.replace(exe)          # откат: возвращаем прежнюю версию на место
        raise UpdateError(f"Не удалось поставить новую версию: {e}. "
                          "Прежняя версия осталась на месте.") from e
    try:
        # запускается ровно тот файл, которым программа только что себя заменила
        subprocess.Popen([str(exe)], close_fds=True)
    except OSError as e:
        raise UpdateError(f"Новая версия установлена, но не запустилась: {e}. "
                          "Запустите программу заново вручную.") from e


def update(release: Release, progress: Callable[[int, int], None] | None = None) -> None:
    """Полный цикл: скачать -> проверить -> подменить -> запустить новую версию."""
    if not is_frozen():
        raise UpdateError("Программа запущена из исходников — обновите её через git pull.")
    _, new_file, _ = _paths()
    install_and_restart(download(release, new_file, progress))
