"""Плагин: статистика стационара по DBF (текстовый/CSV/HTML отчёт)."""

from __future__ import annotations

from omsreg.gui.spec import ActionSpec, JobResult, ParamKind, ParamSpec, RunContext, UtilitySpec
from omsreg.utils import stat_stacionar as stat

_DF = stat.DEFAULT_FIELDS  # {"kotd": "KOTD", ...}


def _run(ctx: RunContext) -> JobResult:
    p = ctx.params
    fields = {"kotd": p["field_kotd"], "kmkb": p["field_kmkb"], "stoim": p["field_stoim"],
              "ishod": p["field_ishod"], "fact": p["field_fact"]}
    res = stat.run_stat(p["target"], p["day_kotd"] or "10,15", fields,
                        extra_handlers=[ctx.log_handler], console=False)
    return JobResult(
        summary=(f"Готово. Случаев: {res['cases']}. Файлы: "
                 f"{res['txt_path'].name}, {res['csv_path'].name}, {res['html_path'].name}"),
        log_text=res["text"],
        open_path=res["html_path"],
    )


def _field(key: str, label: str, df_key: str, legacy: str) -> ParamSpec:
    return ParamSpec(key, label, ParamKind.TEXT, default=_DF[df_key],
                     advanced=True, group="fields", width=14, legacy_key=legacy)


SPEC = UtilitySpec(
    id="stat",
    order=30,
    title="Статистика стационара",
    description=(
        "Отчёт по DBF стационара: дневной/круглосуточный → диагнозы (МКБ) → исходы, "
        "с суммами, средней и мин/макс стоимостью. Сохраняется .txt, .csv (Excel) и .html. "
        "Койко-дни (поле FACT) — необязательно; если поля нет, раздел пропускается."
    ),
    params=(
        ParamSpec("target", "DBF-файл или папка:", ParamKind.PATH, required=True,
                  filetypes=(("DBF", "*.dbf"),),
                  require_msg="Укажите DBF-файл или папку.", legacy_key="статистика_путь"),
        ParamSpec("day_kotd", "Коды отделений ДС:", ParamKind.TEXT,
                  default="10,15", width=18,
                  hint="через запятую; остальные отделения — круглосуточный стационар",
                  legacy_key="дневной_стационар_коды"),
        _field("field_kotd", "Отделение (KOTD):", "kotd", "поле_отделение"),
        _field("field_kmkb", "Код МКБ:", "kmkb", "поле_мкб"),
        _field("field_stoim", "Стоимость:", "stoim", "поле_стоимость"),
        _field("field_ishod", "Исход:", "ishod", "поле_исход"),
        _field("field_fact", "Койко-дни:", "fact", "поле_койко_дни"),
    ),
    actions=(ActionSpec("build", "Построить отчёт", "Accent.TButton"),),
    run=_run,
)
