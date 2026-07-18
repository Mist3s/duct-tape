"""Плагин: удаление ошибочных талонов по протоколам проверки (*.txt)."""

from __future__ import annotations

from omsreg.core import TALON_FIELD_DEFAULT
from omsreg.gui.spec import ActionSpec, JobResult, ParamKind, ParamSpec, RunContext, UtilitySpec
from omsreg.utils import remove_error_talons as talons


def _run(ctx: RunContext) -> JobResult:
    p = ctx.params
    r = talons.run_removal(
        p["dir"], p.get("common") or None, p.get("field") or TALON_FIELD_DEFAULT, p["dry"],
        extra_handlers=[ctx.log_handler], console=False,
    )
    n, files = r["deleted_total"], r["files_changed"]
    if r["had_error"]:
        return JobResult("Работа завершена с ошибками. Подробности в журнале.",
                         had_error=True, box_kind="warning", open_path=r["log_path"])
    if r["dry_run"]:
        return JobResult(f"Проверка завершена. Под удаление попадает записей: {n}.\n"
                         "Файлы не изменялись. Для удаления нажмите «Удалить».",
                         open_path=r["log_path"])
    return JobResult(f"Готово. Удалено записей: {n}, изменено файлов: {files}.\n"
                     "Резервные копии сохранены в папке backup_… рядом с данными.",
                     open_path=r["log_path"])


def _confirm(p: dict) -> str:
    return (f"Будут БЕЗВОЗВРАТНО удалены записи из DBF-файлов в папке:\n{p['dir']}\n\n"
            "Перед изменением каждого файла создаётся резервная копия (папка backup_…).\n\n"
            "Продолжить удаление?")


SPEC = UtilitySpec(
    id="talons",
    order=10,
    title="Удаление по протоколам",
    description=(
        "Из DBF-файлов удаляются коды талонов, перечисленные в протоколах проверки *.txt "
        "(ДВ.txt, ДРЗ.txt и т.д.), лежащих в той же папке. Коды каждого протокола удаляются "
        "из его файла и из общего файла талонов."
    ),
    params=(
        ParamSpec("dir", "Папка с протоколами и DBF:", ParamKind.DIR, required=True,
                  require_msg="Укажите папку с протоколами и DBF.",
                  legacy_key="папка_протоколы"),
        ParamSpec("common", "Общий файл талонов:", ParamKind.FILE, advanced=True,
                  filetypes=(("DBF", "*.dbf"),),
                  hint="по умолчанию ищется сам — файл вида 6_..._t.dbf",
                  legacy_key="общий_файл_талонов"),
        ParamSpec("field", "Поле кода талона:", ParamKind.TEXT, default=TALON_FIELD_DEFAULT,
                  advanced=True, width=18, legacy_key="поле_кода_талона"),
    ),
    actions=(
        ActionSpec("dry", "Проверить (не изменять)", "Accent.TButton", inject={"dry": True}),
        ActionSpec("delete", "Удалить", "Danger.TButton", destructive=True, inject={"dry": False}),
    ),
    run=_run,
    confirm_message=_confirm,
)
