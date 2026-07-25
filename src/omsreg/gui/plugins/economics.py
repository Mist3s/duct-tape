"""Плагин: экономика и эффективность стационара (доходность койки, прерванность, КСГ)."""

from __future__ import annotations

from omsreg.core import money
from omsreg.gui.plugin_fields import (
    DAY_KOTD_DEFAULT,
    day_kotd_param,
    dbf_field_param,
    kotd_names_param,
    target_param,
)
from omsreg.gui.spec import ActionSpec, JobResult, ParamSpec, RunContext, UtilitySpec
from omsreg.utils import parse_kotd_names
from omsreg.utils import stat_economics as econ

_EF = econ.ECON_FIELDS


def _run(ctx: RunContext) -> JobResult:
    p = ctx.params
    fields = {k: p[f"field_{k}"] for k in _EF if f"field_{k}" in p}
    kotd_names = parse_kotd_names(p.get("kotd_names", ""))
    res = econ.run_economics(p["target"], p["day_kotd"] or DAY_KOTD_DEFAULT, fields, kotd_names,
                             extra_handlers=[ctx.log_handler], console=False)
    return JobResult(
        summary=(f"Готово. Случаев: {res['cases']}, оплачено {money(res['total'])} ₽, "
                 f"недополучено {money(res['underpaid'])} ₽.\n"
                 f"Файлы: {res['txt_path'].name}, {res['html_path'].name}"),
        log_text=res["text"],
        open_path=res["html_path"],
    )


def _fld(key: str, label: str) -> ParamSpec:
    return dbf_field_param(f"field_{key}", label, _EF[key], width=12)


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
        target_param(),
        day_kotd_param(),
        kotd_names_param(),
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
