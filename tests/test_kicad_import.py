import pytest

from pcbcreator.kicad_import import KICAD_SHARE, KicadLibrary, parse_sexpr
from pcbcreator.models import PinType

needs_kicad = pytest.mark.skipif(not (KICAD_SHARE / "symbols").exists(), reason="KiCad libraries not installed")


def test_sexpr_parser_handles_quotes_and_nesting():
    tree = parse_sexpr('(a "b c" (d "e\\"f") 1.5)')
    assert tree == [["a", "b c", ["d", 'e"f'], "1.5"]]


@needs_kicad
def test_regulator_pinout_comes_from_kicad_with_ground_fixed():
    lib = KicadLibrary()
    ldo = lib.get("Regulator_Linear:AP2112K-3.3")
    by = {p.name: p for p in ldo.pins}
    assert by["VIN"].type == PinType.power_in and by["VOUT"].type == PinType.power_out
    assert by["GND"].type == PinType.ground  # KiCad says power_in; importer normalises
    assert by["NC"].type == PinType.nc
    assert ldo.footprint == "Package_TO_SOT_SMD:SOT-23-5"
    assert ldo.pinout_source == "kicad:Regulator_Linear:AP2112K-3.3"
    assert not ldo.verified  # electrical limits still need a human


@needs_kicad
def test_generic_passives_and_headers_get_hand_solderable_footprints():
    lib = KicadLibrary()
    assert lib.get("Device:R").passive_kind == "resistor" and lib.get("Device:R").verified
    assert lib.get("Device:C").footprint.endswith("C_0805_2012Metric")
    assert lib.get("Device:LED").footprint.startswith("LED_SMD")
    hdr = lib.get("Connector_Generic:Conn_01x02")
    assert hdr.footprint == "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical" and hdr.verified


@needs_kicad
def test_unknown_part_is_none():
    assert KicadLibrary().get("Nope:Nothing") is None


@needs_kicad
def test_hand_built_ldo_board_with_real_kicad_parts_lints_clean():
    """Human-written fixture (NOT LLM output): proves the linter works with real KiCad pin data."""
    from pcbcreator.lint import lint
    from pcbcreator.models import Instance, Module, Net, Port

    lib = KicadLibrary()
    m = Module(
        name="ldo_board", description="5 V to 3.3 V LDO with power LED",
        parts=[Instance(ref="J1", part="Connector_Generic:Conn_01x02"),
               Instance(ref="J2", part="Connector_Generic:Conn_01x02"),
               Instance(ref="U1", part="Regulator_Linear:AP2112K-3.3"),
               Instance(ref="C1", part="Device:C", value="1uF"), Instance(ref="C2", part="Device:C", value="1uF"),
               Instance(ref="R1", part="Device:R", value="1k"), Instance(ref="D1", part="Device:LED")],
        nets=[Net(name="GND", pins=["J1.2", "J2.2", "U1.2", "C1.2", "C2.2", "D1.1"]),
              Net(name="+5V", voltage=5.0, pins=["J1.1", "U1.1", "U1.3", "C1.1"]),
              Net(name="+3V3", voltage=3.3, pins=["J2.1", "U1.5", "C2.1", "R1.1"]),
              Net(name="LED_A", pins=["R1.2", "D1.2"])],
        ports=[Port(net="+5V", direction="power_in", kind="power", voltage=5.0)],
    )
    # NC pin U1.4 is intentionally left unconnected
    report = lint(m, lib)
    assert report.passed, report.to_text()
    assert not lint(m, lib, strict=True).passed  # AP2112K electrical limits are not human-confirmed yet
