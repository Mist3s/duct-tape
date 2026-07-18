"""Плагин: удаление записей по списку кодов из всех DBF-файлов папки."""

from __future__ import annotations

from omsreg.core import TALON_FIELD_DEFAULT
from omsreg.gui.spec import ActionSpec, JobResult, ParamKind, ParamSpec, RunContext, UtilitySpec
from omsreg.utils import remove_codes as codes


def _run(ctx: RunContext) -> JobResult:
    p = ctx.params
    r = codes.run_codes(
        p["dir"], p.get("codes_file") or None, p.get("field") or TALON_FIELD_DEFAULT, p["dry"],
        int(p["min_len"]), int(p["max_len"]), codes_text=p.get("codes_text") or None,
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


def _validate(p: dict) -> str | None:
    if int(p["min_len"]) > int(p["max_len"]):
        return "Минимальная длина кода больше максимальной."
    if not (p.get("codes_file", "").strip() or p.get("codes_text", "").strip()):
        return "Укажите файл со списком кодов ИЛИ вставьте коды в поле ниже."
    return None


SPEC = UtilitySpec(
    id="codes",
    order=20,
    title="Удаление по списку кодов",
    description=(
        "Коды талонов удаляются из ВСЕХ DBF-файлов папки, где есть поле кода. Список кодов "
        "можно указать файлом ИЛИ вставить/ввести прямо в программе (поле ниже) — числа по "
        "одному в строке либо через пробел/запятую. Если поле заполнено, берётся оно."
    ),
    params=(
        ParamSpec("dir", "Папка с DBF-файлами:", ParamKind.DIR, required=True,
                  require_msg="Укажите папку с DBF-файлами.", legacy_key="папка_коды"),
        ParamSpec("codes_file", "Файл со списком кодов:", ParamKind.FILE,
                  filetypes=(("Текст", "*.txt"), ("Все файлы", "*.*")),
                  hint="или вставьте/введите коды в поле ниже — тогда файл не нужен",
                  legacy_key="файл_кодов"),
        ParamSpec("codes_text", "", ParamKind.TEXTAREA, height=5, persist=False,
                  hint="по одному в строке или через пробел/запятую; при заполнении имеет приоритет над файлом"),
        ParamSpec("field", "Поле кода талона:", ParamKind.TEXT, default=TALON_FIELD_DEFAULT,
                  advanced=True, width=18, legacy_key="поле_кода_талона_список"),
        ParamSpec("min_len", "Длина кода, цифр: от", ParamKind.INT, default=6,
                  advanced=True, min=1, max=20, width=4, legacy_key="длина_кода_мин"),
        ParamSpec("max_len", "Длина кода, цифр: до", ParamKind.INT, default=12,
                  advanced=True, min=1, max=20, width=4, legacy_key="длина_кода_макс"),
    ),
    actions=(
        ActionSpec("dry", "Проверить (не изменять)", "Accent.TButton", inject={"dry": True}),
        ActionSpec("delete", "Удалить", "Danger.TButton", destructive=True, inject={"dry": False}),
    ),
    run=_run,
    validate=_validate,
    confirm_message=_confirm,
)
