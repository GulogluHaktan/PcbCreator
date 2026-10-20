import pytest

from pcbcreator.kicad_import import KICAD_SHARE, KicadLibrary
from pcbcreator.models import Instance, Net
from pcbcreator.stepwise import Block, BlockPatch, scope_findings

needs_kicad = pytest.mark.skipif(not (KICAD_SHARE / "symbols").exists(), reason="KiCad libraries not installed")
LONG = "reasoning that is long enough to satisfy the schema minimum length requirement"


@needs_kicad
def test_an_led_cannot_be_added_in_the_regulator_block_but_capacitors_can():
    lib = KicadLibrary()
    patch = BlockPatch(reasoning=LONG, parts=[Instance(ref="U1", part="Regulator_Linear:AP2112K-3.3"),
                                              Instance(ref="C1", part="Device:C", value="1uF"),
                                              Instance(ref="D1", part="Device:LED")], nets=[Net(name="X", pins=[])])
    owned = {"linear_regulator", "led", "connector"}
    found = scope_findings(patch, Block("regulator", "linear_regulator", "x"), lib, owned)
    assert [f.location for f in found] == ["D1"] and found[0].rule == "E205"
    assert [f.location for f in scope_findings(patch, Block("led", "led", "x"), lib, owned)] == ["U1"]
