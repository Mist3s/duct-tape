"""Плагин: смертность стационара из выгрузки «Отчёт по умершим» (текст/CSV/HTML)."""

from __future__ import annotations

from omsreg.gui.spec import ActionSpec, JobResult, ParamKind, ParamSpec, RunContext, UtilitySpec
from omsreg.utils import stat_deaths as deaths


def _run(ctx: RunContext) -> JobResult:
    p = ctx.params
    res = deaths.run_deaths(
        p["target"],
        groups=(p.get("groups") or None),
        patho_priority=bool(p.get("patho_priority")),
        extra_handlers=[ctx.log_handler],
        console=False,
    )
    summary = (f"Готово. Умерших: {res['deaths']}. Файлы: "
               f"{res['txt_path'].name}, {res['xlsx_path'].name}, {res['html_path'].name}")
    if res["unclassified"]:
        summary += f". Кодов вне справочника: {res['unclassified']} (см. «Прочие» в отчёте)"
    return JobResult(summary=summary, log_text=res["text"], open_path=res["html_path"])


SPEC = UtilitySpec(
    id="deaths",
    order=50,
    title="Смертность стационара",
    description=(
        "Отчёт «Смертность» из выгрузки «Отчёт по умершим» (файл .xls/.html): раскладывает "
        "умерших по месяцам и группам причин смерти (класс МКБ), как ручной отчёт "
        "СМЕРТНОСТЬ … СТАЦИОНАР. Сохраняется .xlsx (Excel, как образец), .html и .txt. "
        "Распределение по группам берётся из справочника МКБ→группа (можно указать свой файл)."
    ),
    params=(
        ParamSpec("target", "Файл выгрузки (.xls/.html):", ParamKind.FILE, required=True,
                  filetypes=(("Отчёт по умершим", "*.xls *.xlsx *.html *.htm"), ("Все файлы", "*.*")),
                  require_msg="Укажите файл выгрузки «Отчёт по умершим»."),
        ParamSpec("groups", "Справочник групп (CSV):", ParamKind.FILE, advanced=True,
                  filetypes=(("CSV", "*.csv"), ("Все файлы", "*.*")),
                  hint="необязательно; по умолчанию встроенный mkb_death_groups.csv"),
        ParamSpec("patho_priority", "Патологоанатомический диагноз приоритетнее клинического",
                  ParamKind.BOOL, default=False, advanced=True),
    ),
    actions=(ActionSpec("build", "Построить отчёт", "Accent.TButton"),),
    run=_run,
)
