import json

from pcbcreator import examples
from pcbcreator.agents import Architecture, ModulePlan, Requirements, RequiredItem
from pcbcreator.evaluate import run_eval, summarise_log
from pcbcreator.library import Library
from pcbcreator.pipeline import Pipeline, architecture_findings, normalise_architecture
from tests.test_pipeline_memory import ScriptedLLM


def test_log_summary_counts_rules_and_rejections():
    log = ["block 1/3 [led]: attempt 1 rejected with 1 issues", "    ERROR   E051 [D1] LED", "    ERROR   E051 [D1] LED",
           "module power: attempt 2 failed with 1 errors", "    WARNING W090 [X] part",
           "block 2/3 [connector]: not clean after 6 attempts; handing over to the module-level repair loop"]
    s = summarise_log(log)
    assert s == {"block_rejections": 1, "module_attempts_failed": 1, "handed_over": True,
                 "rules": {"E051": 2, "W090": 1}}


def test_eval_runs_every_request_each_round_and_writes_summary(tmp_path):
    lib = Library()
    make = lambda trace: Pipeline(ScriptedLLM(), lib, trace_dir=trace)  # noqa: E731
    reqs = [{"id": "a", "prompt": "fc one"}, {"id": "b", "prompt": "fc two"}]
    recs = run_eval(reqs, 2, tmp_path, make, lib, {"name": "t"}, draw=False, echo=lambda s: None)
    assert [(r["id"], r["run"]) for r in recs] == [("a", 1), ("b", 1), ("a", 2), ("b", 2)]
    assert all(r["status"] == "ok" and r["human_verdict"] is None for r in recs)
    assert "| a | 2 | 2/2 |" in (tmp_path / "summary.md").read_text()
    assert json.loads((tmp_path / "results.json").read_text())["meta"]["runs"] == 2
    assert (tmp_path / "a" / "run1" / "log.txt").exists()


def test_architecture_must_assign_every_checklist_item_to_a_module():
    req = Requirements(summary="s", purpose="p", supply="5V", interfaces=[], constraints=[],
                       required=[RequiredItem(kind="linear_regulator", detail="3.3 V LDO"),
                                 RequiredItem(kind="sensor", detail="barometer")])
    p1 = ModulePlan(name="power", purpose="p", ports=[], required=[RequiredItem(kind="linear_regulator", detail="LDO")])
    p2 = ModulePlan(name="sense", purpose="s", ports=[])
    problems = architecture_findings(Architecture(modules=[p1, p2], links=[]), req)
    assert len(problems) == 1 and "sensor" in problems[0].message
    p2 = p2.model_copy(update={"required": [RequiredItem(kind="sensor", detail="baro")]})
    assert architecture_findings(Architecture(modules=[p1, p2], links=[]), req) == []
    one = normalise_architecture(Architecture(modules=[ModulePlan(name="x", purpose="p", ports=[])], links=[]), req)
    assert [r.kind for r in one.modules[0].required] == ["linear_regulator", "sensor"]
