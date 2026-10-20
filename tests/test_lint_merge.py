import pytest

from pcbcreator import examples
from pcbcreator.kicad import export_netlist
from pcbcreator.library import Library
from pcbcreator.lint import lint
from pcbcreator.merge import check_contract, merge
from pcbcreator.models import Design, Instance, Link, Net, Port

LIB = Library()
MODULES = [examples.power_module, examples.mcu_module, examples.imu_module,
           examples.baro_module, examples.io_module]


def rules(report):
    return {f.rule for f in report.findings}


@pytest.mark.parametrize("make", MODULES)
def test_reference_modules_have_no_errors(make):
    report = lint(make(), LIB)
    assert report.passed, report.to_text()


def test_strict_mode_rejects_unverified_parts():
    report = lint(examples.imu_module(), LIB, strict=True)
    assert not report.passed and "E090" in rules(report)


def test_reference_flight_controller_merges_clean():
    result = merge(examples.flight_controller(), LIB)
    errors = [f for f in result.findings if f.severity == "error"]
    assert result.ok, errors
    m = result.module
    assert len({i.ref for i in m.parts}) == len(m.parts)  # unique refs
    nets = {n.name for n in m.nets}
    assert {"GND", "+3V3", "+5V", "SPI_SCK", "PWM1"} <= nets
    assert sum(1 for n in m.nets if n.name == "GND") == 1


def test_netlist_export_resolves_every_node():
    result = merge(examples.flight_controller(), LIB)
    text = export_netlist(result.module, LIB)
    assert text.count("(comp ") == len(result.module.parts)
    assert '(name "GND")' in text and "(export" in text


# --- single-fault injections: each must be caught by the intended rule ----------
def mutated(make, fn):
    m = make()
    fn(m)
    return m


def test_missing_decoupling_cap():
    m = mutated(examples.imu_module, lambda m: m.nets[0].pins.remove("C1.1"))
    assert "E040" in rules(lint(m, LIB))


def test_unknown_part_and_pin():
    m = mutated(examples.imu_module, lambda m: m.parts.append(Instance(ref="X1", part="NOPE")))
    assert "E001" in rules(lint(m, LIB))
    m = mutated(examples.imu_module, lambda m: m.nets[0].pins.append("U1.99"))
    assert "E002" in rules(lint(m, LIB))


def test_pin_on_two_nets_and_duplicate_ref():
    m = mutated(examples.imu_module, lambda m: m.nets[1].pins.append("U1.1"))
    assert "E003" in rules(lint(m, LIB))
    m = mutated(examples.imu_module, lambda m: m.parts.append(Instance(ref="C1", part="C")))
    assert "E004" in rules(lint(m, LIB))


def test_unconnected_pin():
    m = mutated(examples.imu_module, lambda m: m.nets.pop(-1))  # drop IMU_INT net
    m.ports = [p for p in m.ports if p.net != "IMU_INT"]
    assert "E010" in rules(lint(m, LIB))


def test_supply_voltage_out_of_range():
    def bump(m):
        m.nets[0].voltage = 5.0
        m.ports[0].voltage = 5.0
    assert "E021" in rules(lint(mutated(examples.imu_module, bump), LIB))


def test_power_net_without_source():
    m = mutated(examples.imu_module, lambda m: setattr(m, "ports", [p for p in m.ports if p.net != "+3V3"]))
    assert "E020" in rules(lint(m, LIB))


def test_two_outputs_on_one_net():
    def short(m):
        m.nets[2].pins.append("U1.5")  # SPI_SCK net also gets SDO output
    m = mutated(examples.imu_module, short)
    assert "E003" in rules(lint(m, LIB))  # SDO already on SPI_MISO
    mcu = examples.mcu_module()
    sck = next(n for n in mcu.nets if n.name == "SPI_SCK")
    mcu.parts.append(Instance(ref="U9", part="IMU_SPI"))
    sck.pins.append("U9.5")  # second output on SPI_SCK
    assert "E030" in rules(lint(mcu, LIB))


def test_input_without_driver():
    m = mutated(examples.imu_module, lambda m: setattr(m, "ports", [p for p in m.ports if p.net != "SPI_SCK"]))
    assert "E031" in rules(lint(m, LIB))


def test_ground_pin_on_non_ground_net():
    def rename(m):
        next(n for n in m.nets if n.name == "GND").name = "SIGNAL"
        for p in m.ports:
            if p.net == "GND":
                p.net = "SIGNAL"
    assert "E060" in rules(lint(mutated(examples.imu_module, rename), LIB))


def test_i2c_pullups_checked_after_merge_not_in_module():
    d = examples.flight_controller()
    mcu = next(m for m in d.modules if m.name == "mcu")
    mcu.parts = [p for p in mcu.parts if p.ref not in ("R3", "R4")]
    for n in mcu.nets:
        n.pins = [x for x in n.pins if not x.startswith(("R3.", "R4."))]
    assert "E050" not in rules(lint(baro := examples.baro_module(), LIB)) and baro
    result = merge(d, LIB)
    assert any(f.rule == "E050" for f in result.findings)


# --- merge contract checks -----------------------------------------------------
def test_merge_voltage_mismatch():
    d = examples.flight_controller()
    next(m for m in d.modules if m.name == "imu").ports[0].voltage = 1.8
    assert "E103" in {f.rule for f in merge(d, LIB).findings}


def test_merge_two_drivers():
    d = examples.flight_controller()
    d.links.append(Link(a="imu.SPI_MISO", b="io.UART_RX"))
    assert "E105" in {f.rule for f in merge(d, LIB).findings}


def test_merge_unlinked_required_port():
    d = examples.flight_controller()
    d.links = [l for l in d.links if l.a != "mcu.PWM1"]
    assert "E107" in {f.rule for f in merge(d, LIB).findings}
    d.expose.append("mcu.PWM1")
    d.expose.append("io.PWM1")
    assert "E107" not in {f.rule for f in merge(d, LIB).findings}


def test_merge_bad_endpoint():
    d = examples.flight_controller()
    d.links.append(Link(a="mcu.NOPE", b="imu.SPI_SCK"))
    result = merge(d, LIB)
    assert not result.ok and result.findings[0].rule == "E100"


def test_merge_protocol_mismatch():
    d = examples.flight_controller()
    next(m for m in d.modules if m.name == "baro").ports[2].protocol = "spi"
    assert "E104" in {f.rule for f in merge(d, LIB).findings}


def test_contract_check():
    expected = [Port(net="+3V3", direction="power_in", kind="power", voltage=3.3),
                Port(net="MISSING", direction="out"),
                Port(net="SPI_SCK", direction="out", protocol="spi")]
    found = {f.rule for f in check_contract(examples.imu_module(), expected)}
    assert {"E110", "E111"} <= found


def test_design_roundtrips_through_json():
    d = examples.flight_controller()
    assert Design.model_validate_json(d.model_dump_json()) == d
