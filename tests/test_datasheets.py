from pcbcreator.datasheets import DatasheetStore, chunk_text
from pcbcreator.library import Library
from pcbcreator.memory import DesignMemory
from pcbcreator.lint import lint
from pcbcreator.models import Instance, Module, Net


def test_chunking_splits_long_text_and_caps_count():
    text = "\n\n".join(f"paragraph {i} " + "word " * 80 for i in range(1500))
    chunks = chunk_text(text)
    assert len(chunks) == 500 and all(len(c) <= 1400 for c in chunks)  # capped, none oversized
    assert 1 < len(chunk_text("\n\n".join(f"paragraph {i} " + "word " * 80 for i in range(20)))) < 500


def test_store_returns_relevant_passage_for_a_part(tmp_path):
    mem = DesignMemory(str(tmp_path / "c"))
    store = DatasheetStore(mem, tmp_path / "pdf")
    store.add_text("X:LDO", "Absolute maximum ratings: input voltage 6 V.\n\nDropout voltage is 250 mV at 600 mA.\n\nPackage outline drawing.")
    store.add_text("X:OTHER", "Op-amp gain bandwidth product is 10 MHz.")
    assert store.has("X:LDO") and not store.has("X:NONE")
    out = store.excerpts("X:LDO", "dropout voltage", n=2)
    assert "250 mV" in out and "Op-amp" not in out


def test_unknown_part_gets_a_did_you_mean_hint():
    lib = Library()
    m = Module(name="m", parts=[Instance(ref="R1", part="r")], nets=[Net(name="N", pins=["R1.1", "R1.2"])])
    hint = next(f.hint for f in lint(m, lib).findings if f.rule == "E001")
    assert "R" in hint
