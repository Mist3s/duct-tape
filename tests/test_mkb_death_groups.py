"""Справочник распределения причин смерти: загрузка правил и классификация кодов МКБ."""

from omsreg.utils._shared.mkb_death_groups import (
    classify,
    group_order,
    load_rules,
    subgroup_order,
)


def test_rules_loaded():
    r = load_rules()
    assert len(r) >= 10
    gs = group_order(r)
    assert gs[0] == "Болезни нервной системы"          # порядок = порядок столбцов отчёта
    assert "Болезни кровообращения" in gs


def test_classify_by_chapter():
    r = load_rules()
    assert classify("G93.4", r)[0] == "Болезни нервной системы"
    assert classify("J18.9", r)[0] == "Болезни органов дыхания"
    assert classify("K70.3", r)[0] == "Болезни органов пищеварения"
    assert classify("N18.9", r)[0] == "Болезни мочеполовой системы"
    assert classify("D64.8", r)[0] == "Болезни крови и кроветворных органов"


def test_ibs_and_fibrillation_subgroups():
    r = load_rules()
    assert classify("I25.5", r) == ("Болезни кровообращения", "ИБС")
    assert classify("I21.2", r) == ("Болезни кровообращения", "ИБС")
    assert classify("I48.0", r) == ("Болезни кровообращения", "фибрилляция")
    assert classify("I42.8", r) == ("Болезни кровообращения", "")   # прочее кровообращение
    assert subgroup_order(r, "Болезни кровообращения") == ["ИБС", "фибрилляция"]


def test_cerebrovascular_goes_to_nervous():
    # инсульты I60–I69 отнесены к нервной системе (решение по отчёту), а не к кровообращению
    r = load_rules()
    assert classify("I63.9", r)[0] == "Болезни нервной системы"
    assert classify("I67.8", r)[0] == "Болезни нервной системы"


def test_unknown_and_neoplasms_unclassified():
    r = load_rules()
    assert classify("C34.9", r) == ("", "")     # новообразование — вне групп образца
    assert classify("D48.0", r) == ("", "")     # D00–D48 не «кровь» (это D50–D89)
    assert classify("", r) == ("", "")
    assert classify("бред", r) == ("", "")


def test_integer_part_matching():
    # сопоставление по целой части кода: I25.9 попадает в диапазон I20–I25
    r = load_rules()
    assert classify("I25.9", r) == ("Болезни кровообращения", "ИБС")
    assert classify("I26.0", r) == ("Болезни кровообращения", "")   # ТЭЛА — уже не ИБС


def test_external_groups_file(tmp_path):
    p = tmp_path / "g.csv"
    p.write_text("# свой справочник\nкод_от;код_до;группа;подгруппа\n"
                 "A00;B99;Инфекции;\n", encoding="utf-8")
    r = load_rules(str(p))
    assert classify("A41.9", r) == ("Инфекции", "")
    assert classify("I25.5", r) == ("", "")     # в своём справочнике кровообращения нет
