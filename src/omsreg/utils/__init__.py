"""omsreg.utils — доменные утилиты обработки реестров ОМС.

Структура плоская: один модуль — одна утилита (CLI-команда и вкладка интерфейса):
  remove_error_talons — удаление записей DBF по протоколам проверки (*.txt);
  remove_codes        — удаление записей DBF по списку кодов талонов;
  stat_stacionar      — статистика стационара (отделения, МКБ, исходы);
  stat_economics      — экономика стационара (оплата по КСГ, недооплата, доходность койки);
  stat_deaths         — смертность стационара (месяцы × группы причин смерти).

Каждый модуль даёт функцию main() (CLI-команда) и импортируемую run_* — именно её
оборачивают плагины графического интерфейса. Общий код домена и построители отчётов
лежат в приватном подпакете _shared (stat_common, removal_common, mkb_death_groups,
*_report); имена, нужные другим слоям, реэкспортируются здесь — см. __all__. Вся работа
с DBF, логированием и резервным копированием берётся из omsreg.core.
"""

__all__ = [
    "DAY_KOTD_DEFAULT",
    "KOTD_NAMES",
    "format_kotd_names",
    "parse_kotd_names",
]

from omsreg.utils._shared.stat_common import (
    DAY_KOTD_DEFAULT,
    KOTD_NAMES,
    format_kotd_names,
    parse_kotd_names,
)
