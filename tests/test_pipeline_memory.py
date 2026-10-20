from pcbcreator import examples
from pcbcreator.agents import (Architecture, ModulePlan, PartQuery, PartScoutPlan, Requirements, RequiredItem,
                               PartSelection, Review, ReviewIssue)
from pcbcreator.library import Library
from pcbcreator.lint import lint
from pcbcreator.memory import DesignMemory
from pcbcreator.models import Module, Port
from pcbcreator.pipeline import Pipeline

LIB = Library()


class ScriptedLLM:
    """Plays the roles from the hand-written reference design. `broken_first` makes
    the first attempt for one module lack its decoupling cap to exercise the repair loop."""

    def __init__(self, broken_first: str | None = None):
        self.design = examples.flight_controller()
        self.broken_first = broken_first
        self.review_result = Review(reasoning="checked every item of the checklist and found no problem", issues=[], verdict="pass")
        self.calls: list[tuple[str, bool]] = []  # (schema, had_feedback)

    def complete_json(self, system, user, schema):
        self.calls.append((schema.__name__, "rejected with" in user))
        if schema is Requirements:
            return Requirements(summary="quadcopter flight controller", purpose="fly", supply="4S",
                                interfaces=["spi imu", "i2c baro", "4 esc", "uart rx"], constraints=[])
        if schema is Architecture:
            return Architecture(
                modules=[ModulePlan(name=m.name, purpose=m.description, ports=m.ports) for m in self.design.modules],
                links=self.design.links, expose=[])
        if schema is Review:
            return self.review_result
        if schema is PartSelection:
            return PartSelection(reasoning="no special parts are needed here at all", finalists=[])
        if schema is PartScoutPlan:
            return PartScoutPlan(queries=[PartQuery(query="regulator")])
        assert issubclass(schema, Module)
        name = user.split('"name":"')[1].split('"')[0]
        module = next(m for m in self.design.modules if m.name == name).model_copy(deep=True)
        if self.broken_first == name and "rejected with" not in user:
            module.nets[0].pins.remove("C1.1")
        return module


def test_pipeline_happy_path_stores_verified_modules(tmp_path):
    memory = DesignMemory(str(tmp_path / "chroma"))
    res = Pipeline(ScriptedLLM(), LIB, memory).run("build a flight controller", name="fc")
    assert res.status == "ok", res.log
    assert memory.modules.count() == 5
    got = memory.modules.get(include=["metadatas"])
    assert {m["origin"] for m in got["metadatas"]} == {"llm_pending"}  # never trusted until a human approves
    hits = memory.search_modules("spi imu sensor", 1)
    assert hits and hits[0].module.name == "imu"


def test_pipeline_repairs_from_linter_feedback():
    llm = ScriptedLLM(broken_first="imu")
    res = Pipeline(llm, LIB).run("build a flight controller")
    assert res.status == "ok", res.log
    assert any("module imu: attempt 1 failed" in line for line in res.log)
    assert ("DesignedModule", True) in llm.calls  # the retry carried linter feedback


def test_pipeline_gives_up_and_stores_nothing_on_persistent_failure(tmp_path):
    class Stubborn(ScriptedLLM):
        def complete_json(self, system, user, schema):
            out = super().complete_json(system, user, schema)
            if issubclass(schema, Module) and out.name == "imu":
                out.nets[0].pins.remove("C1.1")
            return out

    memory = DesignMemory(str(tmp_path / "chroma"))
    res = Pipeline(Stubborn(), LIB, memory, max_module_repairs=1, max_restarts=1).run("fc")
    assert res.status == "failed" and memory.modules.count() == 0
    assert any("restart 1/1" in line for line in res.log)


def test_strict_pipeline_rejects_unverified_seed_parts():
    res = Pipeline(ScriptedLLM(), LIB, strict=True, max_module_repairs=0).run("fc")
    assert res.status == "failed"


def test_memory_refuses_failing_designs(tmp_path):
    import pytest

    memory = DesignMemory(str(tmp_path / "chroma"))
    bad = examples.imu_module()
    bad.nets[0].pins.remove("C1.1")
    with pytest.raises(ValueError):
        memory.add_module(bad, lint(bad, LIB))
    assert memory.modules.count() == 0


def test_part_retrieval(tmp_path):
    memory = DesignMemory(str(tmp_path / "chroma"))
    memory.index_parts(LIB)
    ids = [h.part_id for h in memory.search_parts("barometer i2c pressure sensor", 3)]
    assert "BARO_I2C" in ids


def test_architecture_contract_rules():
    from pcbcreator.pipeline import architecture_findings

    arch = Architecture(modules=[ModulePlan(name="power", purpose="x", ports=[
        Port(net="VIN", direction="in", kind="power", voltage=5.0),
        Port(net="GND", direction="in", kind="ground"),
        Port(net="S", direction="power_out", kind="signal")])], links=[])
    assert [f.rule for f in architecture_findings(arch)] == ["E073"] * 3


def test_reviewer_major_issue_blocks_and_feeds_back():
    """The LLM reviewer runs only on lint-clean modules; a major issue must send the module back."""
    class OneShotReviewer(ScriptedLLM):
        def __init__(self):
            super().__init__()
            self.reviews = 0

        def complete_json(self, system, user, schema):
            if schema is Review:
                self.reviews += 1
                if self.reviews == 1:
                    return Review(reasoning="the decoupling looks wrong on the main supply rail", issues=[ReviewIssue(severity="major", message="decoupling looks wrong")], verdict="fail")
                return Review(reasoning="checked every item of the checklist and found no problem", issues=[], verdict="pass")
            return super().complete_json(system, user, schema)

    llm = OneShotReviewer()
    res = Pipeline(llm, LIB, review=True).run("fc")
    assert res.status == "ok", res.log
    assert llm.reviews >= 2 and any("attempt 1 failed" in line for line in res.log)


def test_scout_unavailable_part_halts_for_a_human():
    class Stuck(ScriptedLLM):
        def complete_json(self, system, user, schema):
            if schema is PartScoutPlan:
                return PartScoutPlan(queries=[], unavailable=["quantum flux capacitor"])
            return super().complete_json(system, user, schema)

    res = Pipeline(Stuck(), LIB, scout=True).run("fc")
    assert res.status == "needs_human"
