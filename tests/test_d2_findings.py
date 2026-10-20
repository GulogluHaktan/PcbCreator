import pytest

from pcbcreator.agents import RequiredItem
from pcbcreator.coverage import check_coverage
from pcbcreator.kicad_import import KICAD_SHARE, KicadLibrary
from pcbcreator.lint import lint
from pcbcreator.models import Instance, Module, Net

needs_kicad = pytest.mark.skipif(not (KICAD_SHARE / "symbols").exists(), reason="KiCad libraries not installed")


@needs_kicad
def test_two_pin_connector_means_exactly_two_pins():
    """Real run d2_ldo18: two 3-pin headers were used for 'two 2-pin connectors'."""
    lib = KicadLibrary()
    req = [RequiredItem(kind="connector", count=2, detail="2-pin connector for input and output")]
    three = Module(name="m", parts=[Instance(ref="J1", part="Connector_Generic:Conn_01x03"),
                                    Instance(ref="J2", part="Connector_Generic:Conn_01x03")], nets=[])
    two = Module(name="m", parts=[Instance(ref="J1", part="Connector_Generic:Conn_01x02"),
                                  Instance(ref="J2", part="Connector_Generic:Conn_01x02")], nets=[])
    msgs = [f.message for f in check_coverage(three, lib, req)]
    assert len(msgs) == 1 and "exactly 2 pins" in msgs[0]
    assert check_coverage(two, lib, req) == []


@needs_kicad
def test_bypass_pin_tied_to_ground_is_rejected():
    """Real run d2_ldo18: BP of SPX3819M5-L-1-8 was wired straight to GND."""
    lib = KicadLibrary()
    m = Module(name="m", parts=[Instance(ref="U1", part="Regulator_Linear:SPX3819M5-L-1-8"),
                                Instance(ref="C1", part="Device:C", value="10nF")],
               nets=[Net(name="GND", pins=["U1.GND", "U1.BP"]), Net(name="VIN", voltage=3.3, pins=["U1.IN", "U1.EN"]),
                     Net(name="VOUT", voltage=1.8, pins=["U1.OUT"])])
    assert any(f.rule == "E055" for f in lint(m, lib).findings)
    m.nets[0].pins.remove("U1.BP")
    m.nets.append(Net(name="BPN", pins=["U1.BP", "C1.1"]))
    m.nets[0].pins.append("C1.2")
    assert not any(f.rule == "E055" for f in lint(m, lib).findings)
