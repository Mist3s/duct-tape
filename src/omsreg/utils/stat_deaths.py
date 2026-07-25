#!/usr/bin/env python3
"""Отчёт «Смертность стационара» из выгрузки «Отчёт по умершим».

Что делает:
 1. Читает выгрузку «Отчёт по умершим (новый).xls» — это HTML-таблица (экспорт
    мед-системы), одна строка = один умерший: отделение, даты, диагнозы (клинич./
    патологоан.) и т.д. LibreOffice не нужен — таблица разбирается напрямую.
 2. По коду МКБ (клинический диагноз; патологоанатомический — как запас) относит
    каждый случай к группе причин смерти через справочник mkb_death_groups
    (правится в файле _shared/mkb_death_groups.csv или своим через --groups).
 3. Строит матрицу «месяц × группа» (как ручной отчёт СМЕРТНОСТЬ … СТАЦИОНАР):
    столбец «Всего», группы болезней, строка «Итого»; у групп с подгруппами —
    приписка «(в т.ч. ИБС N, фибрилляция N)».
 4. Сохраняет рядом с источником .txt, .csv (Excel), .html и журнал .log.

Сбор данных и запуск — здесь; построение отчётов — в stat.deaths_report.

Примеры запуска:
    omsreg-deaths "Отчет по умершим (новый).xls"
    omsreg-deaths отчёт.xls --groups мой_справочник.csv --patho-priority
"""

from __future__ import annotations

import argparse
import logging
import re
from collections import Counter, defaultdict
from datetime import date, datetime
from html.parser import HTMLParser
from pathlib import Path

from omsreg.core import JobError, setup_job_logging
from omsreg.core.cli import run_or_exit
from omsreg.core.xlsx import write_xlsx
from omsreg.utils._shared.mkb_death_groups import (
    classify,
    group_order,
    load_rules,
    subgroup_order,
)
from omsreg.utils._shared.stat_deaths_report import build_html, build_report, xlsx_columns

log = logging.getLogger("omsreg.utils.stat_deaths")

OTHER_GROUP = "Прочие"
MONTHS_RU = ["", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
             "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]

# ключевые слова заголовка выгрузки -> какое поле это (регистр/хвосты не важны)
_COLS = {"dept": "отдел", "d_out": "дата выпис", "d_in": "дата поступ",
         "age": "возр", "sex": "пол", "fio": "ф.и.о", "diag": "диагноз"}


# ----------------------------- чтение источника (HTML-«xls») -----------------------------

class _TableParser(HTMLParser):
    """Собирает строки первой таблицы как список ячеек (текст, colspan)."""

    def __init__(self):
        super().__init__()
        self.rows: list[list[tuple[str, int]]] = []
        self._row = None
        self._cell = None
        self._colspan = 1

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
            self._colspan = int(dict(attrs).get("colspan", "1") or "1")

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None:
            text = " ".join("".join(self._cell).split())
            self._row.append((text, self._colspan))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None


def _decode(path: Path) -> str:
    data = path.read_bytes()
    for enc in ("utf-8-sig", "cp1251"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def _column_map(header: list[tuple[str, int]]) -> dict:
    """{поле: индекс столбца в данных} по ключевым словам заголовка (с учётом colspan)."""
    cols, idx = {}, 0
    for text, span in header:
        low = text.lower()
        for key, kw in _COLS.items():
            if kw in low and key not in cols:
                cols[key] = idx
        idx += span
    return cols


def read_source(path: Path):
    """Читает выгрузку. -> (список записей-dict, период (date_from, date_to) | None).

    Запись: {ib, fio, sex, age, dept, d_in, d_out, clin, pat}. Даты — date | None."""
    text = _decode(path)
    if "<table" not in text.lower():
        raise ValueError(f"{path.name}: не похоже на HTML-выгрузку «Отчёт по умершим» "
                          "(таблица не найдена)")
    parser = _TableParser()
    parser.feed(text)

    header_i = next((i for i, r in enumerate(parser.rows)
                     if any(_COLS["fio"] in t.lower() or _COLS["dept"] in t.lower()
                            for t, _ in r)), None)
    if header_i is None:
        raise ValueError(f"{path.name}: не найдена шапка таблицы (нет столбцов Ф.И.О./Отделение)")
    cols = _column_map(parser.rows[header_i])
    for need in ("dept", "d_out", "diag"):
        if need not in cols:
            raise ValueError(f"{path.name}: в шапке нет обязательного столбца ({need})")
    clin_i, pat_i = cols["diag"], cols["diag"] + 1

    def cell(row, i):
        return row[i][0].strip() if 0 <= i < len(row) else ""

    records = []
    for row in parser.rows[header_i + 1:]:
        flat = [c for c, _ in row]
        if not flat or not flat[0].strip().isdigit():   # строки данных начинаются с № ИБ (число)
            continue
        records.append({
            "ib": flat[0].strip(),
            "fio": cell(row, cols.get("fio", -1)),
            "sex": cell(row, cols.get("sex", -1)),
            "age": cell(row, cols.get("age", -1)),
            "dept": cell(row, cols["dept"]),
            "d_in": parse_date(cell(row, cols.get("d_in", -1))),
            "d_out": parse_date(cell(row, cols["d_out"])),
            "clin": cell(row, clin_i),
            "pat": cell(row, pat_i),
        })

    # период выгрузки — в заголовке вне таблицы, разбит тегами: снимаем теги и ищем в тексте
    plain = " ".join(re.sub(r"<[^>]+>", " ", text).split())
    m = re.search(r"период\s+с\s+(\d{2}\.\d{2}\.\d{4})\s+по\s+(\d{2}\.\d{2}\.\d{4})", plain, re.I)
    period = (parse_date(m.group(1)), parse_date(m.group(2))) if m else None
    return records, period


def parse_date(s: str):
    """'08.01.2026' -> date. None, если не разобрать."""
    try:
        return datetime.strptime((s or "").strip(), "%d.%m.%Y").date()
    except ValueError:
        return None


# ----------------------------- агрегация (месяц × группа) -----------------------------

def _month_range(a: date, b: date):
    """Список (год, месяц) от a до b включительно."""
    out, y, m = [], a.year, a.month
    while (y, m) <= (b.year, b.month):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def build_matrix(records, rules, patho_priority: bool = False) -> dict:
    """Считает матрицу «месяц × группа» и вспомогательные разрезы. Возвращает dict
    со всем, что нужно отчёту (см. ключи в конце функции)."""
    groups = group_order(rules)
    subs = {g: subgroup_order(rules, g) for g in groups}

    count = defaultdict(int)              # (ym, group) -> число
    subcount = defaultdict(int)           # (ym, group, subgroup) -> число
    total_month = defaultdict(int)        # ym -> всего за месяц
    group_total = defaultdict(int)        # group -> всего
    unclassified = Counter()              # код МКБ без группы -> число
    by_dept = Counter()
    ages = []
    observed = set()
    no_date = 0
    has_other = False

    for r in records:
        code = (r["pat"] or r["clin"]) if patho_priority else (r["clin"] or r["pat"])
        dd = r["d_out"] or r["d_in"]
        if dd is None:
            no_date += 1
            continue
        ym = (dd.year, dd.month)
        observed.add(ym)
        g, sub = classify(code, rules)
        if not g:
            g = OTHER_GROUP
            has_other = True
            unclassified[code or "(без кода)"] += 1
        count[(ym, g)] += 1
        total_month[ym] += 1
        group_total[g] += 1
        if sub:
            subcount[(ym, g, sub)] += 1
        if r["dept"]:
            by_dept[r["dept"]] += 1
        try:
            ages.append(int(r["age"]))
        except (TypeError, ValueError):
            pass

    # какие месяцы показывать: весь период выгрузки + все наблюдавшиеся
    # (период разбираем снаружи и передаём через records? — берём из observed, если нет)
    if observed:
        lo = min(observed)
        hi = max(observed)
        months = _month_range(date(lo[0], lo[1], 1), date(hi[0], hi[1], 1))
        for ym in observed:
            if ym not in months:
                months.append(ym)
        months.sort()
    else:
        months = []

    columns = list(groups) + ([OTHER_GROUP] if has_other else [])
    year = months[0][0] if months else None
    return {
        "months": months, "columns": columns, "subs": subs,
        "count": dict(count), "subcount": dict(subcount),
        "total_month": dict(total_month), "group_total": dict(group_total),
        "grand_total": sum(total_month.values()),
        "unclassified": unclassified, "by_dept": by_dept,
        "ages": ages, "no_date": no_date, "year": year, "n_records": len(records),
    }


# ----------------------------- запуск -----------------------------

def _resolve_source(target) -> Path:
    """Путь к файлу выгрузки: сам файл или единственный xls/xlsx/html в папке."""
    p = Path(target)
    if p.is_dir():
        found = sorted(x for x in p.iterdir()
                       if x.suffix.lower() in (".xls", ".xlsx", ".html", ".htm"))
        if not found:
            raise JobError(f"В папке {p} нет файла-выгрузки (xls/html)")
        if len(found) > 1:
            raise JobError("В папке несколько подходящих файлов — укажите нужный: "
                           + ", ".join(x.name for x in found))
        return found[0]
    if not p.is_file():
        raise JobError(f"Файл не найден: {p}")
    return p


def run_deaths(target, groups=None, patho_priority=False,
               extra_handlers=None, console=True) -> dict:
    """Строит отчёт «Смертность» (.txt/.csv/.html рядом с источником). -> dict с путями."""
    path = _resolve_source(target)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    try:
        rules = load_rules(groups)
    except (OSError, ValueError) as e:
        raise JobError(f"не удалось прочитать справочник групп: {e}") from e
    if not rules:
        raise JobError("справочник групп пуст — проверьте mkb_death_groups.csv или --groups")

    try:
        records, period = read_source(path)
    except (OSError, ValueError) as e:
        raise JobError(str(e)) from e
    if not records:
        raise JobError(f"{path.name}: не найдено ни одной записи об умерших")

    data = build_matrix(records, rules, patho_priority)
    # период из шапки уточняет диапазон месяцев и год отчёта
    if period and period[0] and period[1]:
        pmonths = _month_range(period[0], period[1])
        for ym in data["months"]:
            if ym not in pmonths:
                pmonths.append(ym)
        pmonths.sort()
        data["months"] = pmonths
        data["year"] = period[0].year
    data["period"] = period
    data["source"] = path

    year = data["year"] or datetime.now().year
    base = path.parent / f"smertnost_{year}_{ts}"
    log_path = base.with_suffix(".log")
    setup_job_logging(log, log_path, extra_handlers, console)
    log.info("Источник: %s", path)
    log.info(
        "Умерших в выгрузке: %d%s", data["n_records"],
        f" (без даты выбытия, пропущено: {data['no_date']})" if data["no_date"] else ""
    )
    log.info(
        "Групп в справочнике: %d; период отчёта: %s",
        len(data["columns"]), _period_str(period)
    )
    if data["unclassified"]:
        log.warning(
            "коды МКБ вне справочника (столбец «Прочие»): %s",
            ", ".join(f"{c}×{n}" for c, n in data["unclassified"].most_common())
        )

    text, table = build_report(data)
    txt_path, xlsx_path, html_path = (base.with_suffix(s) for s in (".txt", ".xlsx", ".html"))
    txt_path.write_text(text + "\n", encoding="utf-8")
    # Месяц — по левому краю, «Всего» и группы — по центру (как в образце)
    col_align = {0: "left", **{i: "center" for i in range(1, len(table[0]))}}
    write_xlsx(
        xlsx_path, f"Смертность {year}", table, col_widths=xlsx_columns(data),
        col_align=col_align, header_rows=1, bold_last_row=True
    )
    html_path.write_text(build_html(data), encoding="utf-8")
    log.info("Готово. Файлы: %s, %s, %s", txt_path.name, xlsx_path.name, html_path.name)

    return {
        "text": text, "txt_path": txt_path, "xlsx_path": xlsx_path, "html_path": html_path,
        "log_path": log_path, "deaths": data["grand_total"],
        "unclassified": sum(data["unclassified"].values())
    }


def _period_str(period) -> str:
    if period and period[0] and period[1]:
        return f"{period[0]:%d.%m.%Y}–{period[1]:%d.%m.%Y}"
    return "по датам выбытия"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Отчёт «Смертность стационара» из выгрузки «Отчёт по умершим» (месяц × группа причин).")
    parser.add_argument("source", help="файл выгрузки (xls/html) или папка с ним")
    parser.add_argument("--groups", default=None,
                        help="свой справочник групп (CSV того же формата); по умолчанию встроенный")
    parser.add_argument("--patho-priority", action="store_true",
                        help="брать патологоанатомический диагноз приоритетно (иначе клинический)")
    args = parser.parse_args()
    res = run_or_exit(lambda: run_deaths(args.source, args.groups, args.patho_priority), log)
    print(res["text"])
    print()
    print(f"Отчёт сохранён:      {res['txt_path']}")
    print(f"Файл Excel (.xlsx):  {res['xlsx_path']}")
    print(f"HTML для просмотра:  {res['html_path']}")


if __name__ == "__main__":
    main()
