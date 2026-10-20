from pcbcreator.agents import RequiredItem
from pcbcreator.coverage import check_coverage
from pcbcreator.datasheets import DatasheetStore
from pcbcreator.kicad_import import KICAD_SHARE, KicadLibrary
from pcbcreator.memory import DesignMemory
from pcbcreator.models import Instance, Module, Net
from pcbcreator.partcards import CardStore, PartCard

import pytest

needs_kicad = pytest.mark.skipif(not (KICAD_SHARE / "symbols").exists(), reason="KiCad libraries not installed")


def _module(parts):
    return Module(name="m", parts=[Instance(ref=r, part=p) for r, p in parts],
                  nets=[Net(name="N", pins=[])])


@needs_kicad
def test_coverage_distinguishes_linear_from_switching_regulators_and_counts_connectors():
    lib = KicadLibrary()
    buck = _module([("U1", "Regulator_Switching:LM2574HVM-3.3"), ("J1", "Connector_Generic:Conn_01x02")])
    req = [RequiredItem(kind="linear_regulator", detail="3.3 V LDO"),
           RequiredItem(kind="connector", count=2, detail="in and out"),
           RequiredItem(kind="led", detail="power LED")]
    rules = [f.message for f in check_coverage(buck, lib, req)]
    assert len(rules) == 3 and any("linear_regulator" in m for m in rules)
    good = _module([("U1", "Regulator_Linear:AP2112K-3.3"), ("J1", "Connector_Generic:Conn_01x02"),
                    ("J2", "Connector_Generic:Conn_01x02"), ("D1", "Device:LED")])
    assert check_coverage(good, lib, req) == []


@needs_kicad
def test_part_card_is_extracted_once_and_cached(tmp_path):
    lib = KicadLibrary()
    part = lib.get("Regulator_Linear:AP2112K-3.3")

    class FakeLLM:
        calls = 0

        def complete_json(self, system, user, schema):
            FakeLLM.calls += 1
            assert "Dropout voltage is 250 mV" in user  # the datasheet passage reached the prompt
            return PartCard(part_type="linear regulator (LDO)", vin_min=3.8, vin_max=6.0, vout=3.3,
                            required_external_parts=["1uF input capacitor"], confirmed=True)  # the model lies

    mem = DesignMemory(str(tmp_path / "c"))
    ds = DatasheetStore(mem, tmp_path / "pdf")
    ds.add_text(part.id, "Dropout voltage is 250 mV at 600 mA.\n\nInput voltage range 3.8 V to 6 V.")
    store = CardStore(FakeLLM(), ds, tmp_path / "cards")
    c1, c2 = store.get(part), store.get(part)
    assert FakeLLM.calls == 1 and c1 == c2
    assert "Vin 3.8-6.0 V" in c1.one_line(part.id) and "[unconfirmed]" in c1.one_line(part.id)
    assert c1.confirmed is False  # a model-set confirmation is discarded
