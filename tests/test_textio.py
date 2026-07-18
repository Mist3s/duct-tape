"""Автоопределение кодировок и защита от неверных файлов."""

import pytest

from omsreg.core.textio import detect_and_read_text, read_codes_file


def test_read_codes_utf8_length_filter(tmp_path):
    p = tmp_path / "codes.txt"
    p.write_text("111111\n222222 333333\n5\n1234567890123456\n", encoding="utf-8")
    codes, enc, too_long, too_short = read_codes_file(p, 6, 12)
    assert codes == [111111, 222222, 333333]
    assert too_short == ["5"]
    assert too_long == ["1234567890123456"]  # 16-значный полис отсеян


def test_read_codes_utf16_bom(tmp_path):
    p = tmp_path / "codes.txt"
    p.write_bytes("111111\n222222\n".encode("utf-16"))
    codes, enc, _, _ = read_codes_file(p, 6, 12)
    assert codes == [111111, 222222]
    assert "utf-16" in enc


def test_read_codes_rejects_binary(tmp_path):
    p = tmp_path / "notcodes.dbf"
    p.write_bytes(bytes(range(256)) * 8)  # много управляющих байтов
    with pytest.raises(ValueError):
        read_codes_file(p, 6, 12)


def test_detect_protocol_cp1251(tmp_path):
    p = tmp_path / "ДВ.txt"
    text = "Обработан файл: d00902_07.dbf\nкод талона:59371636\n"
    p.write_bytes(text.encode("cp1251"))
    got, enc = detect_and_read_text(p)
    assert "код талона" in got
    assert enc == "cp1251"
