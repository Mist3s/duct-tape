"""Чтение текстовых файлов с автоопределением кодировки.

Две функции — тонкие места, ради которых их и вынесли под тесты:
  * detect_and_read_text — протоколы проверки (*.txt) в cp1251/utf-8/cp866;
  * read_codes_file      — список кодов талонов (utf-8/utf-16/однобайтовые), с
                           отклонением бинарных файлов и фильтром по длине кода.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

# порядок проб для протоколов проверки
TXT_ENCODINGS = ("cp1251", "utf-8-sig", "cp866")
# порядок проб для списка кодов
CODES_ENCODINGS = ("utf-8-sig", "cp1251", "cp866")


def detect_and_read_text(path: Path, log: logging.Logger | None = None) -> tuple[str, str]:
    """Читает текстовый файл, выбирая кодировку по числу найденных ключевых слов.
    Возвращает (текст, имя_кодировки)."""
    raw = Path(path).read_bytes()
    best = None  # (счёт, текст, кодировка)
    keywords = ("обработан файл", "код талона", "ошибок", "талон")
    for enc in TXT_ENCODINGS:
        try:
            text = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        low = text.lower()
        score = sum(low.count(k) for k in keywords)
        if best is None or score > best[0]:
            best = (score, text, enc)
    if best is None:
        # ни одна кодировка не подошла строго — читаем cp1251 с заменой битых байтов
        return raw.decode("cp1251", errors="replace"), "cp1251 (с заменой нечитаемых байтов)"
    if best[0] == 0 and log:
        log.warning(
            "  ВНИМАНИЕ: в файле %s не найдено ключевых слов ни в одной кодировке; "
            "принята кодировка %s",
            Path(path).name,
            best[2],
        )
    return best[1], best[2]


def read_codes_file(path: Path, min_len: int, max_len: int):
    """Читает файл со списком кодов. Возвращает (список int-кодов по порядку, кодировка,
    пропущенные слишком длинные числа, пропущенные слишком короткие числа).

    Бинарные файлы (например, случайно указанный .dbf) отклоняются с ValueError.
    """
    raw = Path(path).read_bytes()
    text, enc = None, None

    # UTF-16 («Юникод» из Блокнота Windows): BOM или высокая доля нулевых байтов
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            text, enc = raw.decode("utf-16"), "utf-16"
        except UnicodeDecodeError:
            pass
    elif raw and raw.count(0) / len(raw) > 0.2:
        try:
            text, enc = raw.decode("utf-16-le"), "utf-16 (без BOM)"
        except UnicodeDecodeError:
            pass

    if text is None:
        # защита от бинарного файла: доля управляющих байтов (кроме \t\r\n и 0x1A)
        sample = raw[:65536]
        ctrl = sum(1 for b in sample if (b < 9 or 13 < b < 32 or b == 127) and b != 26)
        if sample and ctrl / len(sample) > 0.02:
            raise ValueError(
                f"{Path(path).name}: файл не похож на текстовый список кодов (бинарные данные). "
                f"Укажите обычный текстовый файл с кодами талонов."
            )
        for e in CODES_ENCODINGS:
            try:
                text = raw.decode(e)
                enc = e
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            text, enc = raw.decode("cp1251", errors="replace"), "cp1251 (с заменой нечитаемых байтов)"
        if enc in ("cp1251", "cp866"):
            # для цифр (ASCII) однобайтовые кодировки неразличимы — не утверждаем лишнего
            enc = "однобайтовая (cp1251/cp866)"

    codes, too_long, too_short = [], [], []
    for token in re.findall(r"\d+", text):
        if len(token) > max_len:
            too_long.append(token)
        elif len(token) < min_len:
            too_short.append(token)
        else:
            codes.append(int(token))
    return codes, enc, too_long, too_short
