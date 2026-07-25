"""Мини-генератор .xlsx: корректный zip, числа/строки, экранирование, ширины столбцов."""

import xml.etree.ElementTree as ET
import zipfile

from omsreg.core.xlsx import _CELL_XFS, _DATA_STYLE, STYLE_HEADER, write_xlsx

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _sheet(path):
    return ET.fromstring(zipfile.ZipFile(path).read("xl/worksheets/sheet1.xml"))


def test_valid_zip_and_parts(tmp_path):
    p = tmp_path / "t.xlsx"
    write_xlsx(p, "Лист", [["A", "B"], ["строка", 3]], col_widths={0: 20})
    assert zipfile.is_zipfile(p)
    names = zipfile.ZipFile(p).namelist()
    for part in ("[Content_Types].xml", "xl/workbook.xml", "xl/styles.xml",
                 "xl/worksheets/sheet1.xml"):
        assert part in names


def test_number_vs_string_cells(tmp_path):
    p = tmp_path / "t.xlsx"
    write_xlsx(p, "Л", [["h"], ["текст"], [42]], header_rows=1)
    root = _sheet(p)
    cells = list(root.iter(NS + "c"))
    # число записано как <v>, строка — как inlineStr
    num = [c for c in cells if c.find(NS + "v") is not None and c.get("t") != "inlineStr"]
    strs = [c for c in cells if c.get("t") == "inlineStr"]
    assert any(c.find(NS + "v").text == "42" for c in num)
    assert strs and all(c.find(NS + "is") is not None for c in strs)


def test_escaping_and_widths(tmp_path):
    p = tmp_path / "t.xlsx"
    write_xlsx(p, "Л", [["a & b <c>"]], col_widths={0: 15})
    xml = zipfile.ZipFile(p).read("xl/worksheets/sheet1.xml").decode("utf-8")
    assert "&amp;" in xml and "&lt;c&gt;" in xml   # спецсимволы экранированы
    root = _sheet(p)
    col = root.find(NS + "cols").find(NS + "col")
    assert col.get("width") == "15"


def test_styles_count_matches_definitions(tmp_path):
    """Атрибут count в cellXfs совпадает с числом <xf>: иначе Excel игнорирует оформление."""
    p = tmp_path / "t.xlsx"
    write_xlsx(p, "Л", [["h"], ["x"]], header_rows=1)
    styles = ET.fromstring(zipfile.ZipFile(p).read("xl/styles.xml"))
    cell_xfs = styles.find(NS + "cellXfs")
    assert int(cell_xfs.get("count")) == len(cell_xfs.findall(NS + "xf")) == len(_CELL_XFS)
    # все индексы, которыми пользуется генератор, существуют в таблице стилей
    assert max([STYLE_HEADER, *_DATA_STYLE.values()]) < len(_CELL_XFS)


def test_styles_relationship_present(tmp_path):
    """Без связи workbook -> styles.xml Excel не подхватывает стили (проверено на практике)."""
    p = tmp_path / "t.xlsx"
    write_xlsx(p, "Л", [["h"], ["x"]])
    rels = zipfile.ZipFile(p).read("xl/_rels/workbook.xml.rels").decode("utf-8")
    assert "styles.xml" in rels
