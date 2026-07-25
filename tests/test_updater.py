"""Обновление программы: сравнение версий, разбор релиза, sha256, подмена файла.

Сеть здесь не используется: запросы к GitHub подменяются, файл «программы» — обычный
файл в tmp_path. Проверяется в том числе то, ради чего механизм и написан осторожно:
несовпадение контрольной суммы отменяет установку, а сбой подмены возвращает прежнюю
версию на место.
"""

from __future__ import annotations

import hashlib
import io
import urllib.error

import pytest

from omsreg.core import updater
from omsreg.core.updater import Release, UpdateError, check_latest, is_newer

ASSET = updater.ASSET_NAME


def _release_json(tag="v1.5.0", *, name=ASSET, digest=None, size=42, body="что нового"):
    asset = {"name": name, "size": size,
             "browser_download_url": f"https://github.com/x/y/releases/download/{tag}/{name}"}
    if digest is not None:
        asset["digest"] = digest
    return {"tag_name": tag, "body": body, "html_url": f"https://github.com/x/y/releases/{tag}",
            "assets": [asset]}


def _fake_api(monkeypatch, payload):
    """Подменяет ответ GitHub API готовым словарём."""
    monkeypatch.setattr(updater, "_fetch_json", lambda _url, _timeout: payload)


# ----------------------------- версии -----------------------------

@pytest.mark.parametrize(("candidate", "installed", "newer"), [
    ("1.5.0", "1.4.0", True),
    ("1.4.1", "1.4.0", True),
    ("1.10.0", "1.9.9", True),      # сравнение числами, а не строками
    ("v1.5.0", "1.5.0", False),     # ведущая «v» не мешает
    ("1.4.0", "1.5.0", False),
    ("1.5.0", "1.5.0-rc1", True),   # релиз новее своего release candidate
    ("1.5.0-rc1", "1.5.0", False),
    ("мусор", "1.0.0", False),      # неразбираемая версия никогда не «новее»
    ("", "1.0.0", False),
])
def test_is_newer(candidate, installed, newer):
    assert is_newer(candidate, installed) is newer


# ----------------------------- проверка релиза -----------------------------

def test_check_finds_update(monkeypatch):
    _fake_api(monkeypatch, _release_json(digest="sha256:abc123"))
    result = check_latest("1.4.0")
    assert result.update_available is True
    assert result.latest == "1.5.0"
    assert result.release.sha256 == "abc123"
    assert result.release.tag == "v1.5.0"
    assert "Доступна версия 1.5.0" in result.message


def test_check_up_to_date(monkeypatch):
    _fake_api(monkeypatch, _release_json(tag="v1.4.0"))
    result = check_latest("1.4.0")
    assert result.update_available is False
    assert result.release is None
    assert "последняя версия" in result.message


def test_check_local_version_ahead(monkeypatch):
    """Версия из исходников новее опубликованной — не предлагаем «обновиться» назад."""
    _fake_api(monkeypatch, _release_json(tag="v1.4.0"))
    result = check_latest("1.5.0")
    assert result.update_available is False
    assert "новее опубликованной" in result.message


def test_check_without_tag_raises(monkeypatch):
    _fake_api(monkeypatch, {"assets": []})
    with pytest.raises(UpdateError, match="нет номера версии"):
        check_latest("1.4.0")


def test_check_release_without_digest(monkeypatch):
    """Релиз без опубликованной суммы: обновление доступно, sha256 пустой."""
    _fake_api(monkeypatch, _release_json())
    result = check_latest("1.4.0")
    assert result.update_available is True
    assert result.release.sha256 == ""


def test_check_ignores_foreign_assets(monkeypatch):
    _fake_api(monkeypatch, _release_json(name="README.txt"))
    result = check_latest("1.4.0")
    assert result.release.url == ""      # нужного файла в релизе нет
    with pytest.raises(UpdateError, match="нет файла"):
        updater.download(result.release, dest=None)


# ----------------------------- сетевые ошибки -> понятный текст -----------------------------

def _raise(exc):
    def opener(*_a, **_kw):
        raise exc
    return opener


@pytest.mark.parametrize(("exc", "text"), [
    (urllib.error.HTTPError("u", 404, "Not Found", {}, None), "нет ни одного релиза"),
    (urllib.error.HTTPError("u", 403, "rate limit", {}, None), "ограничил число запросов"),
    (urllib.error.HTTPError("u", 500, "Server Error", {}, None), "ошибкой 500"),
    (urllib.error.URLError("нет сети"), "Нет связи с GitHub"),
    (TimeoutError(), "не ответил за отведённое время"),
    (OSError("обрыв TLS"), "Не удалось обратиться к GitHub"),
])
def test_network_errors_become_readable(monkeypatch, exc, text):
    monkeypatch.setattr(updater.urllib.request, "urlopen", _raise(exc))
    with pytest.raises(UpdateError, match=text):
        check_latest("1.4.0")


def test_broken_json_becomes_readable(monkeypatch):
    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    monkeypatch.setattr(updater.urllib.request, "urlopen",
                        lambda *_a, **_kw: _Resp("<html>не json</html>".encode()))
    with pytest.raises(UpdateError, match="неожиданный ответ"):
        check_latest("1.4.0")


# ----------------------------- скачивание и sha256 -----------------------------

class _Download(io.BytesIO):
    """Ответ на скачивание: тело + заголовок Content-Length."""

    def __init__(self, data):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


def _release_for(data: bytes, *, sha: str | None = None) -> Release:
    digest = hashlib.sha256(data).hexdigest() if sha is None else sha
    return Release(version="1.5.0", tag="v1.5.0", url="https://example/duct-tape.exe",
                   size=len(data), sha256=digest, notes="", page="https://example")


def test_download_verifies_checksum(monkeypatch, tmp_path):
    data = b"\x4d\x5a" + b"program" * 100
    monkeypatch.setattr(updater.urllib.request, "urlopen",
                        lambda *_a, **_kw: _Download(data))
    seen = []
    dest = tmp_path / "new.exe"
    updater.download(_release_for(data), dest, progress=lambda got, total: seen.append((got, total)))
    assert dest.read_bytes() == data
    assert seen and seen[-1] == (len(data), len(data))   # прогресс доходит до конца


def test_download_rejects_wrong_checksum(monkeypatch, tmp_path):
    """Подменённый файл не должен остаться на диске."""
    monkeypatch.setattr(updater.urllib.request, "urlopen",
                        lambda *_a, **_kw: _Download("чужой файл".encode()))
    dest = tmp_path / "new.exe"
    with pytest.raises(UpdateError, match="Контрольная сумма"):
        updater.download(_release_for("чужой файл".encode(), sha="0" * 64), dest)
    assert not dest.exists()


def test_download_network_error_removes_partial_file(monkeypatch, tmp_path):
    monkeypatch.setattr(updater.urllib.request, "urlopen",
                        _raise(urllib.error.URLError("обрыв")))
    dest = tmp_path / "new.exe"
    with pytest.raises(UpdateError, match="Не удалось скачать"):
        updater.download(_release_for(b"x"), dest)
    assert not dest.exists()


# ----------------------------- подмена файла программы -----------------------------

def _pretend_frozen(monkeypatch, exe):
    """Изображает собранный .exe: is_frozen() -> True, current_exe() -> exe."""
    monkeypatch.setattr(updater, "is_frozen", lambda: True)
    monkeypatch.setattr(updater, "current_exe", lambda: exe)


def test_install_swaps_and_starts(monkeypatch, tmp_path):
    exe = tmp_path / "duct-tape.exe"
    exe.write_bytes("старая версия".encode())
    new = tmp_path / "duct-tape.new.exe"
    new.write_bytes("новая версия".encode())
    _pretend_frozen(monkeypatch, exe)
    started = []
    monkeypatch.setattr(updater.subprocess, "Popen", lambda cmd, **_kw: started.append(cmd))

    updater.install_and_restart(new)

    assert exe.read_bytes() == "новая версия".encode()
    assert (tmp_path / "duct-tape.old.exe").read_bytes() == "старая версия".encode()
    assert started == [[str(exe)]]          # запущена именно обновлённая программа
    assert not new.exists()


def test_install_rolls_back_when_replace_fails(monkeypatch, tmp_path):
    """Если новая версия не встала на место, прежняя возвращается — программа жива."""
    exe = tmp_path / "duct-tape.exe"
    exe.write_bytes("старая версия".encode())
    new = tmp_path / "duct-tape.new.exe"
    new.write_bytes("новая версия".encode())
    _pretend_frozen(monkeypatch, exe)

    real_replace = updater.Path.replace

    def replace(self, target):
        if self == new:
            raise OSError("файл занят")
        return real_replace(self, target)

    monkeypatch.setattr(updater.Path, "replace", replace)

    with pytest.raises(UpdateError, match="Прежняя версия осталась на месте"):
        updater.install_and_restart(new)
    assert exe.read_bytes() == "старая версия".encode()


def test_install_from_sources_refuses(monkeypatch, tmp_path):
    monkeypatch.setattr(updater, "is_frozen", lambda: False)
    with pytest.raises(UpdateError, match="git pull"):
        updater.install_and_restart(tmp_path / "new.exe")


def test_cleanup_removes_leftover(monkeypatch, tmp_path):
    exe = tmp_path / "duct-tape.exe"
    exe.write_bytes("я".encode())
    old = tmp_path / "duct-tape.old.exe"
    old.write_bytes("прошлая версия".encode())
    _pretend_frozen(monkeypatch, exe)
    updater.cleanup_leftovers()
    assert not old.exists()


def test_cleanup_silent_when_not_frozen(monkeypatch):
    monkeypatch.setattr(updater, "is_frozen", lambda: False)
    updater.cleanup_leftovers()      # не должно ни падать, ни ничего трогать
