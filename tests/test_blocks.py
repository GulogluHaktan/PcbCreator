import pytest

from pcbcreator.agents import block_examples_context
from pcbcreator.block_patterns import patterns
from pcbcreator.kicad_import import KICAD_SHARE, KicadLibrary
from pcbcreator.memory import DesignMemory
from pcbcreator.models import Port
from pcbcreator.stepwise import Block, apply_patch, local_findings, initial_state

needs_kicad = pytest.mark.skipif(not (KICAD_SHARE / "symbols").exists(), reason="KiCad libraries not installed")


@needs_kicad
def test_human_patterns_are_clean_under_the_same_local_lint_the_model_faces():
    from pcbcreator.agents import ModulePlan

    lib = KicadLibrary()
    for kind, _instr, patch in patterns():
        names = {n.name for n in patch.nets}
        plan = ModulePlan(name="t", purpose="t", ports=[Port(net=n, direction="bidir", kind="ground" if n == "GND" else "signal")
                                                         for n in names])
        state, errs = apply_patch(initial_state(plan, "t"), patch)
        bad = [f for f in local_findings(state, patch, lib) if f.rule != "E010"]  # IC pins are wired by their own block
        assert not errs and not bad, (kind, [str(f) for f in bad])


def test_block_memory_returns_block_level_examples_and_respects_origin(tmp_path):
    mem = DesignMemory(str(tmp_path / "c"))
    for kind, instr, patch in patterns():
        mem.add_block(kind, instr, patch.model_dump_json(), [p.part for p in patch.parts], origin="human")
    block = Block("led", "led", "led: green power indicator LED on the 3.3 V rail")
    text = block_examples_context(mem, block, 1)
    assert "series" in text.lower() and "[human pattern" in text
    assert block_examples_context(mem, block, 1, exclude_origins=["human"]) == "(none)"
