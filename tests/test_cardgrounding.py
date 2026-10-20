from pcbcreator.partcards import PartCard, ground_card

SRC = ("The LP3869x is designed specifically to work with very small ceramic output capacitors. A 1-µF ceramic "
       "capacitor with ESR between 5 mΩ to 500 mΩ is suitable.\nInput voltage range 2.7 V to 10 V.\n"
       "Output current: 800 mA maximum.")


def test_numbers_need_a_real_quote_with_the_right_unit():
    card = PartCard(part_type="LDO", min_output_capacitance_uf=1.0, vin_min=2.7, vin_max=10.0,
                    max_output_current_a=800.0,  # the real failure: 800 mA written as 800 A
                    min_input_capacitance_uf=10.0,  # claimed with no quote at all
                    evidence=["A 1-µF ceramic capacitor with ESR between 5 mΩ to 500 mΩ is suitable.",
                              "Input voltage range 2.7 V to 10 V.", "Output current: 800 mA maximum.",
                              "this sentence is invented and not in the datasheet"])
    g, dropped = ground_card(card, SRC)
    assert g.min_output_capacitance_uf == 1.0 and g.vin_min == 2.7 and g.vin_max == 10.0
    assert g.max_output_current_a is None  # 800 A is not backed: the quote says 800 mA = 0.8 A
    assert g.min_input_capacitance_uf is None
    assert len(g.evidence) == 3 and len(dropped) == 2


def test_correct_milliamp_value_is_kept():
    card = PartCard(part_type="LDO", max_output_current_a=0.8, evidence=["Output current: 800 mA maximum."])
    g, dropped = ground_card(card, SRC)
    assert g.max_output_current_a == 0.8 and dropped == []
