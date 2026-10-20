import pytest

from pcbcreator.agents import Architecture, ModulePlan, Requirements, RequiredItem, Review
from pcbcreator.kicad_import import KICAD_SHARE, KicadLibrary
from pcbcreator.models import Instance, Net, Port
from pcbcreator.pipeline import Pipeline
from pcbcreator.stepwise import BlockPatch, apply_patch, blocks_from_requirements, initial_state, local_findings

needs_kicad = pytest.mark.skipif(not (KICAD_SHARE / "symbols").exists(), reason="KiCad libraries not installed")

PLAN = ModulePlan(name="ledboard", purpose="3.3 V in, power LED", ports=[
    Port(net="+3V3", direction="power_in", kind="power", voltage=3.3),
    Port(net="GND", direction="bidir", kind="ground")])
REQ = Requirements(summary="s", purpose="p", supply="3.3V", interfaces=[], constraints=[],
                   required=[RequiredItem(kind="led", detail="green power LED"),
                             RequiredItem(kind="connector", count=1, detail="2-pin power input")])
LONG = "reasoning that is long enough to satisfy the schema minimum length requirement"


def _conn_patch():
    return BlockPatch(reasoning=LONG, parts=[Instance(ref="J1", part="Connector_Generic:Conn_01x02")],
                      nets=[Net(name="+3V3", pins=["J1.Pin_1"]), Net(name="GND", pins=["J1.Pin_2"])])


def _good_led():
    return BlockPatch(reasoning=LONG, parts=[Instance(ref="R1", part="Device:R", value="680"),
                                              Instance(ref="D1", part="Device:LED", value="green")],
                      nets=[Net(name="+3V3", pins=["R1.1"]), Net(name="LED_A", pins=["R1.2", "D1.A"]),
                            Net(name="GND", pins=["D1.K"])])


def _bad_led():  # LED across the rails, resistor in parallel: the real mistake from the first lint-clean run
    return BlockPatch(reasoning=LONG, parts=[Instance(ref="R1", part="Device:R", value="680"),
                                              Instance(ref="D1", part="Device:LED", value="green")],
                      nets=[Net(name="+3V3", pins=["R1.1", "D1.A"]), Net(name="GND", pins=["R1.2", "D1.K"])])


def test_blocks_come_from_the_checklist_in_a_fixed_order():
    assert [b.kind for b in blocks_from_requirements(REQ)] == ["led", "connector"]


def test_supporting_passives_get_no_block_of_their_own():
    """A resistor listed as its own item once made the LED block get built twice (two parallel resistors)."""
    req = REQ.model_copy(update={"required": REQ.required + [RequiredItem(kind="support", detail="LED resistor")]})
    blocks = blocks_from_requirements(req)
    assert [b.kind for b in blocks] == ["led", "connector"] and "series current-limiting resistor" in blocks[0].instruction


@needs_kicad
def test_patch_merges_into_contract_nets_and_local_lint_only_judges_the_new_block():
    lib = KicadLibrary()
    state = initial_state(PLAN, "ledboard")
    assert [n.name for n in state.nets] == ["+3V3", "GND"] and len(state.ports) == 2
    s1, errs = apply_patch(state, _conn_patch())
    assert not errs and local_findings(s1, _conn_patch(), lib) == []
    s2, _ = apply_patch(s1, _bad_led())
    assert any(f.rule == "E051" for f in local_findings(s2, _bad_led(), lib))
    s3, _ = apply_patch(s1, _good_led())
    assert local_findings(s3, _good_led(), lib) == []
    _, dup = apply_patch(s3, _good_led())  # reusing R1/D1 in a later block is rejected
    assert {f.rule for f in dup} == {"E004"}


@needs_kicad
def test_pipeline_rejects_the_wrong_led_wiring_per_block_then_assembles_a_clean_module():
    class Scripted:
        def __init__(self):
            self.led_calls = 0
            self.led_feedback = []

        def complete_json(self, system, user, schema):
            if schema is Requirements:
                return REQ
            if schema is Architecture:
                return Architecture(modules=[PLAN], links=[], expose=[])
            if schema is BlockPatch:
                if "green power LED" in user:
                    self.led_calls += 1
                    self.led_feedback.append("rejected with" in user)
                    return _bad_led() if self.led_calls == 1 else _good_led()
                return _conn_patch()
            if schema is Review:
                return Review(reasoning="traced the LED current path from the rail through R1 to ground", issues=[],
                              verdict="pass")
            raise AssertionError(schema)

    llm = Scripted()
    res = Pipeline(llm, KicadLibrary(), review=True).run("led board")
    assert res.status == "ok", res.log
    assert llm.led_calls == 2 and llm.led_feedback == [False, True]  # second try carried the E051 feedback
    assert any("attempt 1 rejected" in line for line in res.log)
