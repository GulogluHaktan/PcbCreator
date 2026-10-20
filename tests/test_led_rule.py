import pytest

from pcbcreator.kicad_import import KICAD_SHARE, KicadLibrary
from pcbcreator.lint import lint
from pcbcreator.models import Instance, Module, Net, Port

needs_kicad = pytest.mark.skipif(not (KICAD_SHARE / "symbols").exists(), reason="KiCad libraries not installed")


def _board(nets):
    return Module(
        name="led", parts=[Instance(ref="J1", part="Connector_Generic:Conn_01x02"),
                           Instance(ref="R1", part="Device:R", value="1k6"), Instance(ref="D1", part="Device:LED")],
        nets=nets, ports=[Port(net="+3V3", direction="power_in", kind="power", voltage=3.3)])


@needs_kicad
def test_led_in_parallel_with_its_resistor_is_rejected():
    """The exact mistake the local model made in a real run and that the old linter and reviewer accepted."""
    bad = _board([Net(name="+3V3", voltage=3.3, pins=["J1.Pin_1", "R1.1", "D1.A"]),
                  Net(name="GND", pins=["J1.Pin_2", "R1.2", "D1.K"])])
    findings = [f for f in lint(bad, KicadLibrary()).findings if f.rule == "E051"]
    assert findings and "directly between" in findings[0].message


@needs_kicad
def test_led_with_series_resistor_passes_and_led_without_resistor_fails():
    lib = KicadLibrary()
    good = _board([Net(name="+3V3", voltage=3.3, pins=["J1.Pin_1", "R1.1"]),
                   Net(name="LED_A", pins=["R1.2", "D1.A"]), Net(name="GND", pins=["J1.Pin_2", "D1.K"])])
    assert not [f for f in lint(good, lib).findings if f.rule == "E051"]
    no_r = _board([Net(name="+3V3", voltage=3.3, pins=["J1.Pin_1", "D1.A"]),
                   Net(name="N", pins=["R1.1", "R1.2"]), Net(name="GND", pins=["J1.Pin_2", "D1.K"])])
    assert any(f.rule == "E051" for f in lint(no_r, lib).findings)
