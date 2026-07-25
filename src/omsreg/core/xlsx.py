"""Минимальный генератор .xlsx (одна книга, один лист) без сторонних зависимостей.

Пишет таблицу со стилями, достаточными, чтобы отчёт выглядел как «человеческий»
Excel-файл: жирная шапка с переносом строк, тонкие рамки, заданные ширины столбцов,
жирная итоговая строка. Числа записываются числами, строки — inline-строками; всё
экранируется. Этого хватает, чтобы воспроизвести вид ручного отчёта (ширины колонок,
рамки, заголовки) там, где CSV бессилен (в нём нет ни ширин, ни оформления).

Доменно-нейтрален и не импортирует tkinter — поэтому живёт в core, как report_html.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

# индексы стилей ячеек (s у <c>), см. cellXfs в _STYLES ниже:
# 0 слева · 1 шапка (жирная, по центру, перенос) · 2 жирная слева · 3 по центру ·
# 4 справа · 5 жирная по центру · 6 жирная справа
STYLE_HEADER = 1
# (выравнивание, жирный) -> индекс стиля данных
_DATA_STYLE = {("left", False): 0, ("left", True): 2, ("center", False): 3,
               ("center", True): 5, ("right", False): 4, ("right", True): 6}

_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
    '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
    '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
    '</Types>'
)
_ROOT_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
    '</Relationships>'
)
_WB_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
    # без этой связи со styles.xml читатель не подхватывает таблицу стилей и оформление ячеек пропадает
    '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    '</Relationships>'
)
_STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
    '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
    # Excel требует два зарезервированных заполнения (none, gray125), иначе оформление игнорируется
    '<fills count="2"><fill><patternFill patternType="none"/></fill>'
    '<fill><patternFill patternType="gray125"/></fill></fills>'
    '<borders count="2"><border><left/><right/><top/><bottom/><diagonal/></border>'
    '<border><left style="thin"/><right style="thin"/><top style="thin"/><bottom style="thin"/><diagonal/></border></borders>'
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    '<cellXfs count="7">'
    # 0 обычная слева
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1" applyAlignment="1"><alignment horizontal="left" vertical="center"/></xf>'
    # 1 шапка: жирная, по центру, перенос
    '<xf numFmtId="0" fontId="1" fillId="0" borderId="1" xfId="0" applyFont="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>'
    # 2 жирная слева
    '<xf numFmtId="0" fontId="1" fillId="0" borderId="1" xfId="0" applyFont="1" applyBorder="1" applyAlignment="1"><alignment horizontal="left" vertical="center"/></xf>'
    # 3 по центру
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>'
    # 4 справа
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1" applyAlignment="1"><alignment horizontal="right" vertical="center"/></xf>'
    # 5 жирная по центру
    '<xf numFmtId="0" fontId="1" fillId="0" borderId="1" xfId="0" applyFont="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>'
    # 6 жирная справа
    '<xf numFmtId="0" fontId="1" fillId="0" borderId="1" xfId="0" applyFont="1" applyBorder="1" applyAlignment="1"><alignment horizontal="right" vertical="center"/></xf>'
    '</cellXfs>'
    '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
    '</styleSheet>'
)


def _col_letter(i: int) -> str:
    s, i = "", i + 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _cell_xml(col: int, rownum: int, value, style: int) -> str:
    ref = f"{_col_letter(col)}{rownum}"
    if isinstance(value, bool):
        value = str(value)
    if isinstance(value, (int, float)):
        return f'<c r="{ref}" s="{style}"><v>{value}</v></c>'
    text = escape(str(value))
    return f'<c r="{ref}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'


def write_xlsx(path, sheet_name: str, rows, *, col_widths=None, col_align=None,
               header_rows: int = 1, bold_last_row: bool = False) -> None:
    """Пишет одну таблицу в .xlsx.

    rows          — список списков ячеек (int/float -> число, иначе строка);
    col_widths    — {индекс столбца (с 0): ширина} для нужных столбцов (остальные — по умолчанию);
    col_align     — {индекс столбца: 'left'|'center'|'right'} выравнивание ячеек ДАННЫХ;
                    по умолчанию число — вправо, строка — влево;
    header_rows   — сколько первых строк оформить как жирную шапку с переносом (по центру);
    bold_last_row — выделить жирным последнюю строку (итог).
    """
    col_widths = col_widths or {}
    col_align = col_align or {}
    last = len(rows) - 1
    cols_xml = ""
    if col_widths:
        parts = "".join(f'<col min="{c + 1}" max="{c + 1}" width="{w:g}" customWidth="1"/>'
                        for c, w in sorted(col_widths.items()))
        cols_xml = f"<cols>{parts}</cols>"

    body = []
    for ri, row in enumerate(rows):
        rownum = ri + 1
        is_header = ri < header_rows
        is_bold = bold_last_row and ri == last
        ht = ' ht="30" customHeight="1"' if is_header else ""
        cells = []
        for ci, value in enumerate(row):
            if is_header:
                style = STYLE_HEADER
            else:
                is_num = isinstance(value, (int, float)) and not isinstance(value, bool)
                align = col_align.get(ci, "right" if is_num else "left")
                style = _DATA_STYLE[(align, is_bold)]
            cells.append(_cell_xml(ci, rownum, value, style))
        body.append(f'<row r="{rownum}"{ht}>' + "".join(cells) + "</row>")

    ncols = max((len(r) for r in rows), default=1)
    dim = f"A1:{_col_letter(ncols - 1)}{len(rows) or 1}"
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<dimension ref="{dim}"/><sheetFormatPr defaultRowHeight="15"/>'
        f'{cols_xml}<sheetData>' + "".join(body) + "</sheetData></worksheet>"
    )
    wb = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="{escape(sheet_name)}" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    with zipfile.ZipFile(Path(path), "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES)
        z.writestr("_rels/.rels", _ROOT_RELS)
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/_rels/workbook.xml.rels", _WB_RELS)
        z.writestr("xl/styles.xml", _STYLES)
        z.writestr("xl/worksheets/sheet1.xml", sheet)
