import pytest

from pcbcreator.kicad_import import KICAD_SHARE, KicadLibrary
from pcbcreator.lint import lint
from pcbcreator.models import Instance, Module, Net, Port

needs_kicad = pytest.mark.skipif(not (KICAD_SHARE / "symbols").exists(), reason="KiCad libraries not installed")


def _m(nets):
    return Module(name="c", parts=[Instance(ref="J1", part="Connector_Generic:Conn_01x02")], nets=nets,
                  ports=[Port(net="VIN", direction="power_in", kind="power", voltage=5.0)])


@needs_kicad
def test_connector_with_a_rail_needs_a_ground_pin():
    lib = KicadLibrary()
    bad = _m([Net(name="VIN", voltage=5.0, pins=["J1.Pin_1"]), Net(name="VOUT", voltage=3.3, pins=["J1.Pin_2"])])
    assert any(f.rule == "E053" for f in lint(bad, lib).findings)
    good = _m([Net(name="VIN", voltage=5.0, pins=["J1.Pin_1"]), Net(name="GND", pins=["J1.Pin_2"])])
    assert not any(f.rule == "E053" for f in lint(good, lib).findings)
