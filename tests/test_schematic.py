import shutil

import pytest

from pcbcreator.kicad_import import KICAD_SHARE, KicadLibrary, parse_sexpr
from pcbcreator.models import Instance, Module, Net
from pcbcreator.schematic import build_schematic, dump, flat_symbol, render

needs_kicad = pytest.mark.skipif(not (KICAD_SHARE / "symbols").exists() or shutil.which("kicad-cli") is None,
                                 reason="KiCad not installed")


def _fixture():
    return Module(name="fx", parts=[Instance(ref="U1", part="Regulator_Linear:AP2112K-3.3"),
                                    Instance(ref="C1", part="Device:C", value="1uF"),
                                    Instance(ref="J1", part="Connector_Generic:Conn_01x02")],
                  nets=[Net(name="GND", pins=["U1.GND", "C1.2", "J1.Pin_2"]),
                        Net(name="+5V", pins=["U1.VIN", "U1.EN", "C1.1", "J1.Pin_1"]),
                        Net(name="+3V3", pins=["U1.VOUT"])])


def test_sexpr_roundtrip_keeps_quoting():
    src = '(a "b c" 1.5 yes (d "e"))'
    tree = parse_sexpr(src, keep_quotes=True)[0]
    assert parse_sexpr(dump(tree), keep_quotes=True)[0] == tree


@needs_kicad
def test_extends_is_flattened_and_labels_are_written_for_every_connected_pin():
    assert not [c for c in flat_symbol("Device", "R_US") if isinstance(c, list) and c and c[0] == "extends"]
    text = build_schematic(_fixture(), KicadLibrary())
    assert text.count('(label "+5V"') == 4 and text.count('(label "GND"') == 3


@needs_kicad
def test_kicad_itself_accepts_the_schematic_and_every_connected_pin_is_attached(tmp_path):
    r = render(_fixture(), KicadLibrary(), tmp_path)
    assert r["pdf_ok"] and "pin_not_connected" not in r["erc"]
    assert (tmp_path / "fx.kicad_sch").exists()
