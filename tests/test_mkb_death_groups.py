"""Справочник распределения причин смерти: загрузка правил и классификация кодов МКБ."""

from omsreg.utils._shared.mkb_death_groups import (
    classify,
    group_order,
    load_rules,
    subgroup_order,
)


def _rules():
    """Встроенный справочник; попутно проверяем, что замечаний по нему нет."""
    rules, problems = load_rules()
    assert problems == []
    return rules


def _external(tmp_path, text):
    p = tmp_path / "g.csv"
    p.write_text(text, encoding="utf-8")
    return load_rules(str(p))


def test_rules_loaded():
    r = _rules()
    assert len(r) >= 10
    gs = group_order(r)
    assert gs[0] == "Болезни нервной системы"          # порядок = порядок столбцов отчёта
    assert "Болезни кровообращения" in gs


def test_classify_by_chapter():
    r = _rules()
    assert classify("G93.4", r)[0] == "Болезни нервной системы"
    assert classify("J18.9", r)[0] == "Болезни органов дыхания"
    assert classify("K70.3", r)[0] == "Болезни органов пищеварения"
    assert classify("N18.9", r)[0] == "Болезни мочеполовой системы"
    assert classify("D64.8", r)[0] == "Болезни крови и кроветворных органов"


def test_ibs_and_fibrillation_subgroups():
    r = _rules()
    assert classify("I25.5", r) == ("Болезни кровообращения", "ИБС")
    assert classify("I21.2", r) == ("Болезни кровообращения", "ИБС")
    assert classify("I48.0", r) == ("Болезни кровообращения", "фибрилляция")
    assert classify("I42.8", r) == ("Болезни кровообращения", "")   # прочее кровообращение
    assert subgroup_order(r, "Болезни кровообращения") == ["ИБС", "фибрилляция"]


def test_cerebrovascular_goes_to_nervous():
    # инсульты I60–I69 отнесены к нервной системе (решение по отчёту), а не к кровообращению
    r = _rules()
    assert classify("I63.9", r)[0] == "Болезни нервной системы"
    assert classify("I67.8", r)[0] == "Болезни нервной системы"


def test_unknown_and_neoplasms_unclassified():
    r = _rules()
    assert classify("C34.9", r) == ("", "")     # новообразование — вне групп образца
    assert classify("D48.0", r) == ("", "")     # D00–D48 не «кровь» (это D50–D89)
    assert classify("", r) == ("", "")
    assert classify("бред", r) == ("", "")


def test_integer_part_matching():
    # сопоставление по целой части кода: I25.9 попадает в диапазон I20–I25
    r = _rules()
    assert classify("I25.9", r) == ("Болезни кровообращения", "ИБС")
    assert classify("I26.0", r) == ("Болезни кровообращения", "")   # ТЭЛА — уже не ИБС


def test_code_with_trailing_text():
    # в ячейке диагноза код часто идёт с расшифровкой — код всё равно должен опознаваться
    r = _rules()
    assert classify("I25.5 Атеросклеротическая болезнь сердца", r)[1] == "ИБС"
    assert classify("J18.9, пневмония", r)[0] == "Болезни органов дыхания"


def test_typo_in_code_is_not_guessed():
    # «I2O» — латинская O вместо нуля: это не код I02, а мусор (уйдёт в «Прочие»)
    r = _rules()
    assert classify("I2O", r) == ("", "")
    assert classify("I2О", r) == ("", "")       # и с русской О тоже


def test_external_groups_file(tmp_path):
    r, problems = _external(tmp_path, "# свой справочник\nкод_от;код_до;группа;подгруппа\n"
                                      "A00;B99;Инфекции;\n")
    assert problems == []                       # заголовок и комментарий — не мусор
    assert classify("A41.9", r) == ("Инфекции", "")
    assert classify("I25.5", r) == ("", "")     # в своём справочнике кровообращения нет


def test_header_variants_are_silent(tmp_path):
    r, problems = _external(tmp_path, "Код от;Код до;Группа;Подгруппа\nA00;B99;Инфекции;\n")
    assert problems == [] and len(r) == 1


def test_broken_bound_is_reported(tmp_path):
    # опечатка в границе диапазона не должна молча превращаться в другой диапазон:
    # правило отбрасывается, но о нём сообщается
    r, problems = _external(tmp_path, "I2O;I25;Болезни кровообращения;ИБС\n"
                                      "I00;I99;Болезни кровообращения;\n")
    assert len(r) == 1
    assert len(problems) == 1
    assert "I2O" in problems[0] and "строка 1" in problems[0]
    assert classify("I05.0", r) == ("Болезни кровообращения", "")   # не ИБС, как было бы при I02–I25


def test_missing_upper_bound_is_reported(tmp_path):
    r, problems = _external(tmp_path, "I30;;Нет верхней;\n")
    assert r == []
    assert len(problems) == 1 and "код_до" in problems[0]


def test_too_few_fields_is_reported(tmp_path):
    r, problems = _external(tmp_path, "I30;I31\n")
    assert r == []
    assert len(problems) == 1 and "мало полей" in problems[0]


def test_reversed_range_is_reported(tmp_path):
    r, problems = _external(tmp_path, "I25;I20;Болезни кровообращения;\n")
    assert r == []
    assert len(problems) == 1 and "меньше" in problems[0]


def test_empty_group_is_reported(tmp_path):
    r, problems = _external(tmp_path, "I20;I25;;ИБС\n")
    assert r == []
    assert len(problems) == 1 and "группа" in problems[0]
