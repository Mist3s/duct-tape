#!/usr/bin/env python3
r"""Сборка справочника КСГ (код группы -> наименование, профиль) из официальных xlsx.

Читает листы «КСГ» из файлов «Расшифровка групп КСГ … на 2026 год.xlsx» (дневной и
круглосуточный стационар) и генерирует модуль-данные
src/omsreg/utils/_shared/ksg_catalog.py со словарём KSG.

Берутся ТОЛЬКО наименование и профиль группы — веса/коэффициенты (КЗ и т.п.) в
справочник не кладём: они берутся из самого реестра. Коды дневного стационара
начинаются с «ds…», круглосуточного — с «st…», поэтому пересечений между файлами нет.

Зависимостей нет: xlsx разбирается как zip с XML (стандартная библиотека), как и
всё остальное в проекте.

Запуск (пересобрать при выходе новой версии справочника):
    python tools/build_ksg_catalog.py \
        "…/Расшифровка групп КСГ для дневного стационара на 2026 год.xlsx" \
        "…/Расшифровка групп КСГ для круглосуточного стационара на 2026 год.xlsx"

Необязательно: --out PATH (по умолчанию src/omsreg/utils/_shared/ksg_catalog.py).
"""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
RELS_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
SHEET_NAME = "КСГ"
# заголовки столбцов на листе «КСГ» (сопоставляем по тексту, а не по номеру)
COL_CODE = "КСГ"
COL_NAME = "Наименование КСГ"
COL_PROFILE = "Профиль"

DEFAULT_OUT = Path(__file__).resolve().parents[1] / "src/omsreg/utils/_shared/ksg_catalog.py"


def _col_index(ref: str) -> int:
    """Номер столбца (0-based) из ссылки на ячейку вида 'AB12'."""
    n = 0
    for ch in ref:
        if ch.isalpha():
            n = n * 26 + (ord(ch.upper()) - 64)
        else:
            break
    return n - 1


def _shared_strings(z: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in z.namelist():
        return []
    root = ET.fromstring(z.read("xl/sharedStrings.xml"))
    return ["".join(t.text or "" for t in si.iter(NS + "t")) for si in root.iter(NS + "si")]


def _sheet_path(z: zipfile.ZipFile, name: str) -> str:
    """Путь к XML листа с заданным именем (через workbook.xml + rels)."""
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    rid_by_target = {r.get("Id"): r.get("Target") for r in rels}
    for s in wb.iter(NS + "sheet"):
        if s.get("name") == name:
            target = rid_by_target[s.get(RELS_NS + "id")]
            return target if target.startswith("xl/") else "xl/" + target
    raise SystemExit(f"лист «{name}» не найден в книге")


def _rows(z: zipfile.ZipFile, sheet_path: str, shared: list[str]):
    root = ET.fromstring(z.read(sheet_path))
    for row in root.iter(NS + "row"):
        cells: dict[int, str] = {}
        for c in row.iter(NS + "c"):
            col = _col_index(c.get("r") or "")
            t = c.get("t")
            v = c.find(NS + "v")
            isel = c.find(NS + "is")
            if t == "s" and v is not None:
                val = shared[int(v.text)]
            elif t == "inlineStr" and isel is not None:
                val = "".join(x.text or "" for x in isel.iter(NS + "t"))
            elif v is not None:
                val = v.text or ""
            else:
                val = ""
            cells[col] = val
        yield cells


def _clean(s: str) -> str:
    """Убирает переводы строк и лишние пробелы внутри значения."""
    return re.sub(r"\s+", " ", (s or "").strip())


def parse_file(path: Path) -> dict[str, tuple[str, str]]:
    """{код КСГ (нижний регистр): (наименование, профиль)} из листа «КСГ» одного xlsx."""
    z = zipfile.ZipFile(path)
    shared = _shared_strings(z)
    rows = _rows(z, _sheet_path(z, SHEET_NAME), shared)
    header = next(rows)
    idx = {_clean(v): k for k, v in header.items()}
    for need in (COL_CODE, COL_NAME, COL_PROFILE):
        if need not in idx:
            raise SystemExit(f"{path.name}: на листе «{SHEET_NAME}» нет столбца «{need}» "
                             f"(есть: {', '.join(sorted(idx))})")
    ci, ni, pi = idx[COL_CODE], idx[COL_NAME], idx[COL_PROFILE]
    out: dict[str, tuple[str, str]] = {}
    for cells in rows:
        code = _clean(cells.get(ci, "")).lower()
        if not code:
            continue
        out[code] = (_clean(cells.get(ni, "")), _clean(cells.get(pi, "")))
    return out


def render(catalog: dict[str, tuple[str, str]], sources: list[Path]) -> str:
    """Текст модуля-данных ksg_catalog.py: докстринг + словарь KSG по алфавиту кодов."""
    # перечисление источников — внутри докстринга, поэтому без «#»: это не комментарий
    src_lines = "\n".join(f"  - {p.name}" for p in sources)
    lines = [
        '"""Справочник КСГ: код группы -> (наименование, профиль).',
        "",
        "СГЕНЕРИРОВАНО автоматически, вручную не редактировать. Источник — официальные",
        '«Расшифровка групп КСГ … на 2026 год» (лист «КСГ»):',
        src_lines,
        "",
        "Пересобрать при выходе новой версии справочника:",
        "    python tools/build_ksg_catalog.py <дневной.xlsx> <круглосуточный.xlsx>",
        "",
        "Веса и коэффициенты (КЗ и т.п.) сюда намеренно не включены — они берутся из",
        "самого реестра. Коды дневного стационара начинаются с «ds…», круглосуточного —",
        'с «st…».',
        '"""',
        "",
        "from __future__ import annotations",
        "",
        f"# всего групп: {len(catalog)}",
        "# код КСГ (нижний регистр) -> (наименование КСГ, профиль медицинской помощи)",
        "KSG: dict[str, tuple[str, str]] = {",
    ]
    for code in sorted(catalog):
        name, profile = catalog[code]
        lines.append(f"    {code!r}: ({name!r}, {profile!r}),")
    lines.append("}")
    return "\n".join(lines) + "\n"


def main() -> None:  # noqa: D103 - назначение утилиты в ArgumentParser(description=...), попадает в --help
    ap = argparse.ArgumentParser(description="Сборка ksg_catalog.py из xlsx-справочников КСГ.")
    ap.add_argument("xlsx", nargs="+", type=Path, help="файлы «Расшифровка групп КСГ … .xlsx»")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"куда писать (по умолчанию {DEFAULT_OUT})")
    args = ap.parse_args()

    catalog: dict[str, tuple[str, str]] = {}
    for path in args.xlsx:
        if not path.is_file():
            raise SystemExit(f"файл не найден: {path}")
        part = parse_file(path)
        collisions = [c for c in part if c in catalog and catalog[c] != part[c]]
        for c in collisions:
            print(f"ВНИМАНИЕ: код {c} встречается в нескольких файлах с разными значениями:\n"
                  f"    было {catalog[c]}\n    стало {part[c]}", file=sys.stderr)
        catalog.update(part)
        print(f"{path.name}: {len(part)} групп")

    args.out.write_text(render(catalog, list(args.xlsx)), encoding="utf-8")
    print(f"Записано {len(catalog)} групп -> {args.out}")


if __name__ == "__main__":
    main()
