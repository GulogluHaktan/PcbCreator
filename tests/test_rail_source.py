import pytest

from pcbcreator.kicad_import import KICAD_SHARE, KicadLibrary
from pcbcreator.lint import lint
from pcbcreator.models import Instance, Module, Net

needs_kicad = pytest.mark.skipif(not (KICAD_SHARE / "symbols").exists(), reason="KiCad libraries not installed")


def _led(vcc_net_pins, vin_pins):
    return Module(name="m", parts=[Instance(ref="R1", part="Device:R", value="560"), Instance(ref="D1", part="Device:LED"),
                                   Instance(ref="J1", part="Connector_Generic:Conn_01x02")],
                  nets=[Net(name="VIN", voltage=5.0, pins=vin_pins), Net(name="VCC", voltage=5.0, pins=vcc_net_pins),
                        Net(name="GND", pins=["D1.K", "J1.Pin_2"]), Net(name="LED_A", pins=["R1.2", "D1.A"])])


@needs_kicad
def test_invented_second_rail_is_reported_and_the_correct_wiring_passes():
    """The real d0_led failure: R1 hung on an invented rail 'VCC' that nothing supplies."""
    lib = KicadLibrary()
    bad = [f for f in lint(_led(["R1.1"], ["J1.Pin_1"]), lib).findings if f.rule == "E025"]
    assert len(bad) == 1 and "VCC" in bad[0].message and "no source" in bad[0].message
    good = _led(["R1.1"], ["J1.Pin_1"])
    good.nets = [n for n in good.nets if n.name != "VCC"]
    good.nets[0].pins.append("R1.1")  # R1 on the connector's rail
    assert not [f for f in lint(good, lib).findings if f.rule == "E025"]
