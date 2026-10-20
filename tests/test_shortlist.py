import pytest

from pcbcreator.agents import Finalist, PartSelection, finalist_parts, shortlist
from pcbcreator.kicad_import import KICAD_SHARE, KicadLibrary
from pcbcreator.memory import DesignMemory

needs_kicad = pytest.mark.skipif(not (KICAD_SHARE / "symbols").exists(), reason="KiCad libraries not installed")


@needs_kicad
def test_shortlist_is_a_small_slice_with_real_regulators_and_invented_ids_are_dropped(tmp_path):
    lib = KicadLibrary()
    lib.load_lib("Regulator_Linear")
    lib.load_lib("Regulator_Switching")
    mem = DesignMemory(str(tmp_path / "c"))
    mem.index_library(lib)
    total = mem.parts.count()
    short = shortlist(lib, mem, ["fixed 3.3V low dropout linear regulator", "step-down switching regulator"],
                      per_query=20, cap=40)
    assert 0 < len(short) <= 40 < total
    assert all(p.category == "ic" for p in short)
    ids = [p.id for p in short]
    assert any(i.startswith("Regulator_Linear:") for i in ids) and any(i.startswith("Regulator_Switching:") for i in ids)
    chosen = PartSelection(reasoning="keep the first candidate, drop the invented one", finalists=[Finalist(id=ids[0]), Finalist(id="Regulator_Linear:DOES_NOT_EXIST")])
    final = [p.id for p in finalist_parts(lib, chosen, short)]
    assert ids[0] in final and "Regulator_Linear:DOES_NOT_EXIST" not in final and "Device:R" in final
