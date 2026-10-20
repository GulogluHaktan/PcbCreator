import pytest

from pcbcreator.kicad_import import KICAD_SHARE, KicadLibrary
from pcbcreator.lint import capacitance_uf, lint
from pcbcreator.models import Instance, Module, Net, Port
from pcbcreator.partcards import PartCard

needs_kicad = pytest.mark.skipif(not (KICAD_SHARE / "symbols").exists(), reason="KiCad libraries not installed")


def test_capacitance_parsing():
    assert capacitance_uf("100nF") == pytest.approx(0.1) and capacitance_uf("1uF") == pytest.approx(1.0)
    assert capacitance_uf("10 µF") == pytest.approx(10.0) and capacitance_uf("470p") == pytest.approx(0.00047)
    assert capacitance_uf("x") is None and capacitance_uf(None) is None


def _ldo(cin, cout):
    return Module(name="m", parts=[Instance(ref="U1", part="Regulator_Linear:LP38693DT-3.3"),
                                   Instance(ref="C1", part="Device:C", value=cin), Instance(ref="C2", part="Device:C", value=cout)],
                  nets=[Net(name="VIN", voltage=5.0, pins=["U1.IN", "C1.1"]), Net(name="VOUT", voltage=3.3, pins=["U1.OUT", "C2.1"]),
                        Net(name="GND", pins=["U1.GND", "C1.2", "C2.2"])],
                  ports=[Port(net="VIN", direction="power_in", kind="power", voltage=5.0)])


@needs_kicad
def test_datasheet_minimum_capacitance_is_enforced_from_the_card():
    """Real run: the model put 100 nF on an LDO whose datasheet asks for at least 1 uF."""
    lib = KicadLibrary()
    card = {"Regulator_Linear:LP38693DT-3.3": PartCard(part_type="LDO", min_input_capacitance_uf=1.0,
                                                       min_output_capacitance_uf=1.0)}
    bad = [f for f in lint(_ldo("100nF", "100nF"), lib, cards=card).findings if f.rule == "E054"]
    assert len(bad) == 2 and "at least 1 uF" in bad[0].message
    assert not [f for f in lint(_ldo("1uF", "1uF"), lib, cards=card).findings if f.rule == "E054"]
    assert not [f for f in lint(_ldo("100nF", "100nF"), lib).findings if f.rule == "E054"]  # no card, no claim
