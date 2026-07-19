"""Плагин: экономика и эффективность стационара (доходность койки, прерванность, КСГ)."""

from __future__ import annotations

from omsreg.gui.spec import ActionSpec, JobResult, ParamKind, ParamSpec, RunContext, UtilitySpec
from omsreg.utils import stat_economics as econ
from omsreg.utils import stat_stacionar as stat

_EF = econ.ECON_FIELDS


def _run(ctx: RunContext) -> JobResult:
    p = ctx.params
    fields = {k: p[f"field_{k}"] for k in _EF if f"field_{k}" in p}
    kotd_names = stat.parse_kotd_names(p.get("kotd_names", ""))
    res = econ.run_economics(p["target"], p["day_kotd"] or "10,15,12", fields, kotd_names,
                             extra_handlers=[ctx.log_handler], console=False)
    return JobResult(
        summary=(f"Готово. Случаев: {res['cases']}, оплачено {stat.money(res['total'])} ₽, "
                 f"недополучено {stat.money(res['underpaid'])} ₽.\n"
                 f"Файлы: {res['txt_path'].name}, {res['html_path'].name}"),
        log_text=res["text"],
        open_path=res["html_path"],
    )


def _fld(key: str, label: str) -> ParamSpec:
    return ParamSpec(f"field_{key}", label, ParamKind.TEXT, default=_EF[key],
                     advanced=True, group="fields", width=12)


SPEC = UtilitySpec(
    id="economics",
    order=40,
    title="Экономика стационара",
    description=(
        "Экономика и эффективность стационара: доходность койки (₽ на койко-день), рейтинг "
        "«какая койка платит лучше», прерванные (недооплаченные) случаи и упущенная выручка, "
        "топ КСГ. Оплата по КСГ: STOIM = БС × KOEF_Z × KOEF_UP × KOEF_PR. Сохраняется .txt и .html."
    ),
    params=(
        ParamSpec("target", "DBF-файл или папка:", ParamKind.PATH, required=True,
                  filetypes=(("DBF", "*.dbf"),),
                  require_msg="Укажите DBF-файл или папку.", legacy_key="статистика_путь"),
        ParamSpec("day_kotd", "Коды отделений ДС:", ParamKind.TEXT, default="10,15,12", width=18,
                  hint="через запятую; остальные отделения — круглосуточный стационар",
                  legacy_key="дневной_стационар_коды"),
        ParamSpec("kotd_names", "Названия отделений:", ParamKind.TEXT, advanced=True,
                  default=stat.format_kotd_names(stat.KOTD_NAMES),
                  hint="формат: 23=Пульмонологическое; 27=Терапевтическое; 61=Неврологическое",
                  legacy_key="названия_отделений"),
        _fld("stoim", "Стоимость (STOIM):"),
        _fld("fact", "Койко-дни (FACT):"),
        _fld("kotd", "Отделение (KOTD):"),
        _fld("ishod", "Исход (ISHOD):"),
        _fld("gruppa", "КСГ (GRUPPA):"),
        _fld("koef_z", "KOEF_Z:"),
        _fld("koef_up", "KOEF_UP:"),
        _fld("koef_pr", "KOEF_PR:"),
    ),
    actions=(ActionSpec("build", "Построить отчёт", "Accent.TButton"),),
    run=_run,
)
