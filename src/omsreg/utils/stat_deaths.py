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
 4. Сохраняет рядом с источником .txt, .xlsx (Excel), .html и журнал .log.

Сбор данных и запуск — здесь; построение отчётов — в модуле
omsreg.utils._shared.stat_deaths_report.

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
from typing import NamedTuple

from omsreg.core import JobError, setup_job_logging
from omsreg.core.cli import run_or_exit
from omsreg.core.xlsx import write_xlsx
from omsreg.utils._shared.mkb_death_groups import (
    classify,
    group_order,
    load_rules,
    subgroup_order,
)
from omsreg.utils._shared.stat_deaths_report import (
    build_html,
    build_report,
    period_str,
    xlsx_columns,
)

log = logging.getLogger("omsreg.utils.stat_deaths")

OTHER_GROUP = "Прочие"

# кодировки, в которых мед-системы выгружают «Отчёт по умершим» (в порядке проб)
SOURCE_ENCODINGS = ("utf-8-sig", "cp1251")
# сколько заполненных ячеек делают строку «похожей на данные» (о её потере предупреждаем)
MIN_DATA_CELLS = 3
# сколько значений показывать в примере к предупреждению
MAX_EXAMPLES = 3

# ключевые слова заголовка выгрузки -> какое поле это (регистр/хвосты не важны)
_COLS = {"dept": "отдел", "d_out": "дата выпис", "d_in": "дата поступ",
         "age": "возр", "sex": "пол", "fio": "ф.и.о", "diag": "диагноз"}

# возраст в ячейке «Возр.»: только целое число лет («7 мес.» годами не считаем)
_AGE_RE = re.compile(r"\d{1,3}")


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


class Source(NamedTuple):
    """Прочитанная выгрузка.

    records — записи-словари {ib, fio, sex, age, dept, d_in, d_out, clin, pat}
    (даты — date | None); period — период из шапки (date, date) | None;
    encoding — имя кодировки, в которой файл удалось прочитать; problems —
    замечания для журнала (нераспознанная кодировка, битые даты, пропущенные
    строки). Замечания обязан показать вызывающий: см. run_deaths.
    """

    records: list
    period: tuple | None
    encoding: str
    problems: list


def _examples(values, limit: int = MAX_EXAMPLES) -> str:
    """Первые несколько различных значений через запятую — для текста предупреждения."""
    seen = []
    for v in values:
        if v not in seen:
            seen.append(v)
        if len(seen) >= limit:
            break
    return ", ".join(f"«{v}»" for v in seen)


def _decode(path: Path) -> tuple[str, str]:
    """Текст выгрузки и имя кодировки, в которой её удалось прочитать.

    Если не подошла ни одна из SOURCE_ENCODINGS, читаем utf-8 с заменой
    нечитаемых байтов и честно говорим об этом в имени кодировки: иначе
    «крякозябры» в ФИО и диагнозах выглядели бы как настоящие данные.
    """
    data = path.read_bytes()
    for enc in SOURCE_ENCODINGS:
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace"), "utf-8 (с заменой нечитаемых байтов)"


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


def _find_header(rows, name: str) -> tuple[int, dict]:
    """(индекс строки-шапки, карта столбцов) в разобранной таблице.

    ValueError, если шапки нет или в ней не хватает обязательного столбца, —
    без них дальше работать бессмысленно.
    """
    header_i = next((i for i, r in enumerate(rows)
                     if any(_COLS["fio"] in t.lower() or _COLS["dept"] in t.lower()
                            for t, _ in r)), None)
    if header_i is None:
        raise ValueError(f"{name}: не найдена шапка таблицы (нет столбцов Ф.И.О./Отделение)")
    cols = _column_map(rows[header_i])
    for need in ("dept", "d_out", "diag"):
        if need not in cols:
            raise ValueError(f"{name}: в шапке нет обязательного столбца ({need})")
    return header_i, cols


def _read_records(rows, cols: dict, problems: list) -> list:
    """Записи-словари из строк данных; всё пропущенное — в problems (не молча)."""
    clin_i, pat_i = cols["diag"], cols["diag"] + 1
    bad_dates: list[str] = []
    skipped: list[str] = []

    def cell(row, i):
        return row[i][0].strip() if 0 <= i < len(row) else ""

    def date_cell(row, i):
        """Дата из ячейки; нераспознанное непустое значение запоминаем для журнала."""
        raw = cell(row, i)
        value = parse_date(raw)
        if value is None and raw:
            bad_dates.append(raw)
        return value

    records = []
    for row in rows:
        flat = [c for c, _ in row]
        first = flat[0].strip() if flat else ""
        if not first.isdigit():   # строки данных начинаются с № ИБ (число)
            # служебные строки (второй ярус шапки, итоги) короткие — о них молчим;
            # а вот заполненную строку данных потерять нельзя, даже если № ИБ странный
            if sum(1 for c in flat if c.strip()) >= MIN_DATA_CELLS:
                skipped.append(first or "(пустой № ИБ)")
            continue
        records.append({
            "ib": first,
            "fio": cell(row, cols.get("fio", -1)),
            "sex": cell(row, cols.get("sex", -1)),
            "age": cell(row, cols.get("age", -1)),
            "dept": cell(row, cols["dept"]),
            "d_in": date_cell(row, cols.get("d_in", -1)),
            "d_out": date_cell(row, cols["d_out"]),
            "clin": cell(row, clin_i),
            "pat": cell(row, pat_i),
        })

    if skipped:
        problems.append(f"пропущено строк с нечисловым № ИБ: {len(skipped)} "
                        f"(например: {_examples(skipped)}) — в отчёт они не попали")
    if bad_dates:
        problems.append(f"не разобрано дат: {len(bad_dates)} "
                        f"(ожидается ДД.ММ.ГГГГ, а получено: {_examples(bad_dates)})")
    return records


def _find_period(text: str):
    """Период выгрузки (date, date) из заголовка вне таблицы или None.

    Заголовок разбит тегами, поэтому теги снимаем и ищем «период с … по …»
    в получившемся тексте.
    """
    plain = " ".join(re.sub(r"<[^>]+>", " ", text).split())
    m = re.search(r"период\s+с\s+(\d{2}\.\d{2}\.\d{4})\s+по\s+(\d{2}\.\d{2}\.\d{4})", plain, re.I)
    return (parse_date(m.group(1)), parse_date(m.group(2))) if m else None


def read_source(path: Path) -> Source:
    """Читает выгрузку «Отчёт по умершим» (HTML-таблица) -> Source.

    Ошибки формата (нет таблицы, нет шапки, нет обязательного столбца) —
    ValueError. Всё, что можно пропустить и продолжить (кодировка, битая дата,
    строка без числового № ИБ), попадает в Source.problems, а не теряется молча.
    """
    text, encoding = _decode(path)
    problems: list[str] = []
    if encoding not in SOURCE_ENCODINGS:
        problems.append(f"кодировка не распознана, принята {encoding} — "
                        "проверьте ФИО и диагнозы в отчёте")
    if "<table" not in text.lower():
        raise ValueError(f"{path.name}: не похоже на HTML-выгрузку «Отчёт по умершим» "
                         "(таблица не найдена)")
    parser = _TableParser()
    parser.feed(text)

    header_i, cols = _find_header(parser.rows, path.name)
    records = _read_records(parser.rows[header_i + 1:], cols, problems)
    return Source(records, _find_period(text), encoding, problems)


def parse_date(s: str):
    """'08.01.2026' -> date. None, если не разобрать (единственный ожидаемый формат)."""
    try:
        return datetime.strptime((s or "").strip(), "%d.%m.%Y").date()
    except ValueError:
        return None


def _parse_age(text: str) -> int | None:
    """Возраст в полных годах из ячейки «Возр.»: '75' -> 75, иначе None.

    «7 мес.» намеренно не разбирается: 7 месяцев превратились бы в 7 лет и
    испортили средний возраст. Такие значения run_deaths показывает отдельным
    замечанием в журнале — лучше увидеть их, чем угадывать.
    """
    return int(text) if _AGE_RE.fullmatch(text) else None


# ----------------------------- агрегация (месяц × группа) -----------------------------

def _month_range(a: date, b: date):
    """Список (год, месяц) от a до b включительно."""
    out, y, m = [], a.year, a.month
    while (y, m) <= (b.year, b.month):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _months_axis(observed, period) -> list:
    """Ось месяцев отчёта: (год, месяц) по возрастанию.

    Основа — сплошной диапазон от первого до последнего месяца, в котором есть
    умершие; если известен период выгрузки, показываем его целиком (в т.ч. месяцы
    без смертей), досыпая наблюдавшиеся месяцы за его пределами.
    """
    months: list = []
    if observed:
        lo, hi = min(observed), max(observed)
        months = _month_range(date(lo[0], lo[1], 1), date(hi[0], hi[1], 1))
        months.extend(ym for ym in observed if ym not in months)
        months.sort()
    if period and period[0] and period[1]:
        in_period = _month_range(period[0], period[1])
        in_period.extend(ym for ym in months if ym not in in_period)
        in_period.sort()
        months = in_period
    return months


class DeathsMatrix(NamedTuple):
    """Агрегат отчёта «Смертность» — единственный контракт с построителями отчётов.

    months — ось месяцев (год, месяц); columns — группы причин в порядке
    справочника плюс «Прочие», если он понадобился; subs — {группа: подгруппы};
    count/subcount — числа по (месяц, группа) и (месяц, группа, подгруппа);
    total_month/group_total/grand_total — итоги; unclassified — Counter кодов МКБ
    вне справочника; by_dept — Counter умерших по отделениям; ages — разобранные
    возрасты, age_unparsed — Counter неразобранных значений «Возр.»; no_date —
    записей без даты выбытия и поступления; year — год отчёта (или None);
    n_records — записей в выгрузке; period — период выгрузки (date, date) | None;
    source — файл выгрузки (для шапки отчёта).
    """

    months: list
    columns: list
    subs: dict
    count: dict
    subcount: dict
    total_month: dict
    group_total: dict
    grand_total: int
    unclassified: Counter
    by_dept: Counter
    ages: list
    age_unparsed: Counter
    no_date: int
    year: int | None
    n_records: int
    period: tuple | None = None
    source: Path | None = None

    @property
    def source_name(self) -> str:
        """Имя файла выгрузки для шапки отчёта (если источник не задан — так и пишем)."""
        return self.source.name if self.source else "(источник не указан)"


def build_matrix(records, rules, patho_priority: bool = False,
                 period=None, source=None) -> DeathsMatrix:
    """Считает матрицу «месяц × группа» и вспомогательные разрезы.

    period (период из шапки выгрузки) задаёт ось месяцев и год отчёта, source —
    файл выгрузки для шапки. Оба попадают в результат здесь же: дописывать поля
    снаружи не нужно, build_report(build_matrix(...)) работает как есть.
    """
    groups = group_order(rules)
    subs = {g: subgroup_order(rules, g) for g in groups}

    count = defaultdict(int)              # (ym, group) -> число
    subcount = defaultdict(int)           # (ym, group, subgroup) -> число
    total_month = defaultdict(int)        # ym -> всего за месяц
    group_total = defaultdict(int)        # group -> всего
    unclassified = Counter()              # код МКБ без группы -> число
    age_unparsed = Counter()              # неразобранное значение «Возр.» -> число
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
        age_text = str(r["age"] or "").strip()
        age = _parse_age(age_text)
        if age is not None:
            ages.append(age)
        elif age_text:
            age_unparsed[age_text] += 1

    months = _months_axis(observed, period)
    year = None
    if period and period[0] and period[1]:
        year = period[0].year          # год берём из шапки выгрузки, если она есть
    elif months:
        year = months[0][0]
    return DeathsMatrix(
        months=months,
        columns=list(groups) + ([OTHER_GROUP] if has_other else []),
        subs=subs,
        count=dict(count),
        subcount=dict(subcount),
        total_month=dict(total_month),
        group_total=dict(group_total),
        grand_total=sum(total_month.values()),
        unclassified=unclassified,
        by_dept=by_dept,
        ages=ages,
        age_unparsed=age_unparsed,
        no_date=no_date,
        year=year,
        n_records=len(records),
        period=period,
        source=source,
    )


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


def _log_problems(rule_problems, src: Source, data: DeathsMatrix) -> None:
    """Пишет в журнал задачи всё, что было пропущено при разборе (без этого — молча)."""
    for problem in rule_problems:
        log.warning("справочник групп: %s (правило не применялось)", problem)
    for problem in src.problems:
        log.warning("выгрузка: %s", problem)
    if data.unclassified:
        log.warning(
            "коды МКБ вне справочника (столбец «Прочие»): %s",
            ", ".join(f"{c}×{n}" for c, n in data.unclassified.most_common())
        )
    if data.age_unparsed:
        log.warning(
            "возраст не разобран у %d записей (в средний возраст не вошли): %s",
            sum(data.age_unparsed.values()),
            _examples(list(data.age_unparsed))
        )


def run_deaths(target, groups=None, patho_priority=False,
               extra_handlers=None, console=True) -> dict:
    """Строит отчёт «Смертность» рядом с источником: .txt, .xlsx (Excel), .html и журнал .log.

    -> dict: text (текст отчёта), txt_path/xlsx_path/html_path/log_path и числа
    deaths, unclassified.
    """
    path = _resolve_source(target)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    try:
        rules, rule_problems = load_rules(groups)
    except (OSError, ValueError) as e:
        raise JobError(f"не удалось прочитать справочник групп: {e}") from e
    if not rules:
        raise JobError("справочник групп пуст — проверьте mkb_death_groups.csv или --groups")

    try:
        src = read_source(path)
    except (OSError, ValueError) as e:
        raise JobError(str(e)) from e
    if not src.records:
        raise JobError(f"{path.name}: не найдено ни одной записи об умерших")

    data = build_matrix(src.records, rules, patho_priority, period=src.period, source=path)

    year = data.year or datetime.now().year
    base = path.parent / f"smertnost_{year}_{ts}"
    log_path = base.with_suffix(".log")
    setup_job_logging(log, log_path, extra_handlers, console)
    log.info("Источник: %s (кодировка %s)", path, src.encoding)
    log.info(
        "Умерших в выгрузке: %d%s", data.n_records,
        f" (без даты выбытия, пропущено: {data.no_date})" if data.no_date else ""
    )
    log.info(
        "Групп в справочнике: %d; период отчёта: %s",
        len(data.columns), period_str(src.period)
    )
    _log_problems(rule_problems, src, data)

    text, table = build_report(data)
    txt_path, xlsx_path, html_path = (base.with_suffix(s) for s in (".txt", ".xlsx", ".html"))
    txt_path.write_text(text + "\n", encoding="utf-8")
    # Месяц — по левому краю, «Всего» и группы — по центру (как в образце)
    col_align = {0: "left", **dict.fromkeys(range(1, len(table[0])), "center")}
    write_xlsx(
        xlsx_path, f"Смертность {year}", table, col_widths=xlsx_columns(data),
        col_align=col_align, header_rows=1, bold_last_row=True
    )
    html_path.write_text(build_html(data), encoding="utf-8")
    log.info("Готово. Файлы: %s, %s, %s", txt_path.name, xlsx_path.name, html_path.name)

    return {
        "text": text, "txt_path": txt_path, "xlsx_path": xlsx_path, "html_path": html_path,
        "log_path": log_path, "deaths": data.grand_total,
        "unclassified": sum(data.unclassified.values())
    }


def main() -> None:  # noqa: D103 - назначение утилиты в ArgumentParser(description=...), попадает в --help
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
