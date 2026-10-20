import pytest

from pcbcreator.agents import (Architecture, ModulePlan, RequiredItem, Requirements, code_queries, shortlist)
from pcbcreator.kicad_import import KICAD_SHARE, KicadLibrary
from pcbcreator.memory import DesignMemory
from pcbcreator.models import Port
from pcbcreator.pipeline import architecture_findings, normalise_architecture

needs_kicad = pytest.mark.skipif(not (KICAD_SHARE / "symbols").exists(), reason="KiCad libraries not installed")


def _split():
    power = ModulePlan(name="power", purpose="supply", required=[RequiredItem(kind="connector", detail="header")],
                       ports=[Port(net="VIN", direction="power_in", kind="power", voltage=3.3),
                              Port(net="VCC", direction="power_out", kind="power", voltage=3.3),
                              Port(net="GND", direction="bidir", kind="ground")])
    sense = ModulePlan(name="sensor", purpose="sensing", required=[RequiredItem(kind="sensor", detail="temp")],
                       ports=[Port(net="VCC", direction="power_in", kind="power", voltage=3.3),
                              Port(net="GND", direction="bidir", kind="ground"),
                              Port(net="SDA", direction="bidir"), Port(net="SCL", direction="bidir")])
    return Architecture(modules=[power, sense], links=[])


def test_a_needlessly_split_plan_is_flagged_and_collapsed_unless_modules_were_requested():
    request = "Temperature sensor breakout powered from 3.3 V through a 4-pin header."
    assert any("does not ask" in f.message for f in architecture_findings(_split(), None, request))
    one = normalise_architecture(_split(), None, request)
    assert len(one.modules) == 1 and one.modules[0].name == "board"
    assert [r.kind for r in one.modules[0].required] == ["connector", "sensor"]
    assert sorted(p.net for p in one.modules[0].ports) == ["GND", "SCL", "SDA", "VIN"]  # VCC stayed internal
    asked = "Two-module sensor node: a power module and a sensor module."
    assert not any("does not ask" in f.message for f in architecture_findings(_split(), None, asked))
    assert len(normalise_architecture(_split(), None, asked).modules) == 2


def test_code_queries_carry_the_library_family_and_do_not_repeat_words():
    req = Requirements(summary="s", purpose="p", supply="3V3", interfaces=[], constraints=[],
                       required=[RequiredItem(kind="sensor", detail="Digital I2C temperature sensor")])
    (query, family), = code_queries(req)
    assert family == "Sensor" and query.lower().count("sensor") == 1


@needs_kicad
def test_family_filter_keeps_current_sensors_out_of_a_temperature_search(tmp_path):
    lib = KicadLibrary()
    for name in ("Sensor_Temperature", "Sensor_Current"):
        lib.load_lib(name)
    mem = DesignMemory(str(tmp_path / "c"))
    mem.index_library(lib)
    ids = [p.id for p in shortlist(lib, mem, [("digital temperature sensor I2C", "Sensor_Temperature")], per_query=10)]
    assert ids and all(i.startswith("Sensor_Temperature:") for i in ids)
