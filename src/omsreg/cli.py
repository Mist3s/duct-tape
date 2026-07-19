"""Единая команда `omsreg` с подкомандами.

    omsreg                     — запустить графический интерфейс (то же, что omsreg gui)
    omsreg gui
    omsreg remove-talons ...   — удаление ошибочных талонов по протоколам
    omsreg remove-codes ...    — удаление по списку кодов из всех DBF
    omsreg stat ...            — статистика стационара
    omsreg econ ...            — экономика и эффективность стационара

Каждая подкоманда принимает те же аргументы, что и одноимённая CLI-утилита; за
подробностями — `omsreg <подкоманда> --help`.
"""

from __future__ import annotations

import sys

USAGE = __doc__


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)

    if not argv or argv[0] == "gui":
        from omsreg.gui.app import main as gui_main
        gui_main()
        return

    if argv[0] in ("-h", "--help", "help"):
        print(USAGE)
        return

    cmd, rest = argv[0], argv[1:]

    if cmd == "remove-talons":
        from omsreg.utils.remove_error_talons import main as m
    elif cmd == "remove-codes":
        from omsreg.utils.remove_codes import main as m
    elif cmd == "stat":
        from omsreg.utils.stat_stacionar import main as m
    elif cmd == "econ":
        from omsreg.utils.stat_economics import main as m
    else:
        print(f"Неизвестная подкоманда: {cmd}\n", file=sys.stderr)
        print(USAGE, file=sys.stderr)
        sys.exit(2)

    # каждая утилита разбирает свои аргументы своим argparse
    sys.argv = [f"omsreg {cmd}"] + rest
    m()


if __name__ == "__main__":
    main()
