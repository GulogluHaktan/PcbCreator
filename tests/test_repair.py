import pytest

from pcbcreator.kicad_import import KICAD_SHARE, KicadLibrary
from pcbcreator.lint import lint
from pcbcreator.models import Instance, Module, Net, Port
from pcbcreator.stepwise import RepairPatch, apply_repair

needs_kicad = pytest.mark.skipif(not (KICAD_SHARE / "symbols").exists(), reason="KiCad libraries not installed")
LONG = "reasoning that is long enough to satisfy the schema minimum length requirement"


def _bad():
    return Module(name="m", parts=[Instance(ref="J1", part="Connector_Generic:Conn_01x02"),
                                   Instance(ref="R1", part="Device:R", value="680"), Instance(ref="D1", part="Device:LED")],
                  nets=[Net(name="+3V3", voltage=3.3, pins=["J1.Pin_1", "R1.1", "D1.A"]),
                        Net(name="GND", pins=["J1.Pin_2", "R1.2", "D1.K"])],
                  ports=[Port(net="+3V3", direction="power_in", kind="power", voltage=3.3)])


@needs_kicad
def test_a_small_edit_moves_two_pins_and_fixes_the_led_without_touching_the_rest():
    lib = KicadLibrary()
    assert any(f.rule == "E051" for f in lint(_bad(), lib).findings)
    patch = RepairPatch(reasoning=LONG, parts=[], remove_pins=["D1.A", "R1.2"],
                        nets=[Net(name="LED_A", pins=["R1.2", "D1.A"])])
    fixed, errs = apply_repair(_bad(), patch, lib)
    assert not errs and [p.ref for p in fixed.parts] == ["J1", "R1", "D1"]
    assert not any(f.rule == "E051" for f in lint(fixed, lib).findings)
    nets = {n.name: n.pins for n in fixed.nets}
    assert nets["+3V3"] == ["J1.Pin_1", "R1.1"] and nets["GND"] == ["J1.Pin_2", "D1.K"]


@needs_kicad
def test_removing_a_part_removes_its_pins_from_every_net_and_unknown_refs_are_reported():
    lib = KicadLibrary()
    patch = RepairPatch(reasoning=LONG, parts=[], remove_parts=["R1", "R9"], nets=[])
    fixed, errs = apply_repair(_bad(), patch, lib)
    assert [p.ref for p in fixed.parts] == ["J1", "D1"] and all("R1." not in n for net in fixed.nets for n in net.pins)
    assert [f.location for f in errs] == ["R9"]
