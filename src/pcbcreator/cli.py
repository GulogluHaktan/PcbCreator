"""Command line: pcbc lint | merge | example | design | index-parts"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import examples
from .kicad import export_netlist
from .library import Library
from .lint import lint
from .merge import merge
from .models import Design, Module



def _lib(args: argparse.Namespace) -> Library:
    return Library.from_json(args.library) if args.library else Library()


def cmd_lint(args: argparse.Namespace) -> int:
    module = Module.model_validate_json(Path(args.file).read_text())
    report = lint(module, _lib(args), strict=args.strict)
    print(json.dumps(report.to_dict(), indent=2) if args.json else report.to_text())
    return 0 if report.passed else 1


def cmd_merge(args: argparse.Namespace) -> int:
    lib = _lib(args)
    design = Design.model_validate_json(Path(args.file).read_text())
    result = merge(design, lib, strict=args.strict)
    for f in result.findings:
        print(f)
    print(f"merge: {'OK' if result.ok else 'FAILED'}")
    if result.ok and args.output:
        Path(args.output).write_text(export_netlist(result.module, lib))
        print(f"wrote KiCad netlist {args.output}")
    return 0 if result.ok else 1


def cmd_render(args: argparse.Namespace) -> int:
    """Draw a module as a real KiCad schematic + PDF + PNG and run KiCad's own ERC on it."""
    from .kicad_import import KicadLibrary
    from .schematic import render

    module = Module.model_validate_json(Path(args.file).read_text())
    r = render(module, KicadLibrary(), args.out)
    print("schematic:", r["schematic"], "\npdf:", r.get("pdf_ok"), "\npng:", r.get("png"))
    diff = r["connectivity_diff"]
    print("connectivity: KiCad reads exactly our nets" if not diff else "connectivity MISMATCH:\n  " + "\n  ".join(diff))
    print("KiCad ERC:\n" + r["erc"])
    return 0 if not diff else 1


def cmd_example(args: argparse.Namespace) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    design = examples.flight_controller()
    (out / "flight_controller.design.json").write_text(design.model_dump_json(indent=2))
    for m in design.modules:
        (out / f"{m.name}.module.json").write_text(m.model_dump_json(indent=2))
    print(f"wrote reference design + {len(design.modules)} modules to {out}")
    return 0


def cmd_design(args: argparse.Namespace) -> int:
    from .llm import LlamaServerClient
    from .memory import DesignMemory, make_embedder
    from .pipeline import Pipeline

    memory = DesignMemory(args.memory, make_embedder(args.embedder))
    if args.kicad:
        from .kicad_import import KicadLibrary
        lib = KicadLibrary()  # parts are searched in Chroma (run `pcbc index-kicad` once) and loaded on demand
    else:
        lib = _lib(args)
        memory.index_parts(lib)
    llm = LlamaServerClient(base_url=args.base_url, thinking=args.thinking)
    cards = None
    if args.kicad:
        from .datasheets import DatasheetStore
        from .partcards import CardStore
        cards = CardStore(llm, DatasheetStore(memory, Path(args.memory) / "datasheet_pdfs",
                                      Path(args.memory) / "datasheets_manual"),
                          Path(args.memory) / "part_cards")
    res = Pipeline(llm, lib, memory, strict=args.strict, echo=True, cards=cards, scout=args.kicad,
                   review=not args.no_review, stepwise=not args.flat, trace_dir=Path(args.out) / "trace").run(args.prompt, name=args.name)
    if res.merge and res.merge.module:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{args.name}.merged.json").write_text(res.merge.module.model_dump_json(indent=2))
        if res.status == "ok":
            (out / f"{args.name}.net").write_text(export_netlist(res.merge.module, lib))
        if args.kicad:  # always draw what was built, even when it failed review: you should see the problem
            from .schematic import render
            r = render(res.merge.module, lib, out / "schematic", args.name)
            print("schematic drawing:", r.get("png") or r.get("schematic"))
            if r["connectivity_diff"]:
                print("WARNING: the drawing does not match the netlist:", r["connectivity_diff"])
        for f in res.merge.findings:
            print(f)
    print(f"status: {res.status}")
    return 0 if res.status == "ok" else 1


def cmd_index_parts(args: argparse.Namespace) -> int:
    from .memory import DesignMemory, make_embedder

    DesignMemory(args.memory, make_embedder(args.embedder)).index_parts(_lib(args))
    print("parts indexed")
    return 0


def cmd_index_kicad(args: argparse.Namespace) -> int:
    from .kicad_import import KicadLibrary
    from .memory import DesignMemory, make_embedder

    lib = KicadLibrary()
    lib.load_all()
    n = DesignMemory(args.memory, make_embedder(args.embedder)).index_library(lib)
    print(f"indexed {n} KiCad parts into {args.memory}")
    return 0


def cmd_seed_blocks(args: argparse.Namespace) -> int:
    from .block_patterns import patterns
    from .memory import DesignMemory, make_embedder

    mem = DesignMemory(args.memory, make_embedder(args.embedder))
    for kind, instruction, patch in patterns():
        mem.add_block(kind, instruction, patch.model_dump_json(), [p.part for p in patch.parts],
                      origin="human", source="block_patterns.py")
    print(f"stored {len(patterns())} human-written block patterns (origin=human)")
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    """Run a request list several times and write results.json + summary.md (see evaluate.py)."""
    import subprocess

    from .datasheets import DatasheetStore
    from .evaluate import run_eval
    from .kicad_import import KicadLibrary
    from .llm import LlamaServerClient
    from .memory import DesignMemory, make_embedder
    from .partcards import CardStore
    from .pipeline import Pipeline

    requests = json.loads(Path(args.requests).read_text())
    memory = DesignMemory(args.memory, make_embedder(args.embedder))
    lib = KicadLibrary()
    llm = LlamaServerClient(base_url=args.base_url, thinking=args.thinking)
    cards = CardStore(llm, DatasheetStore(memory, Path(args.memory) / "datasheet_pdfs",
                                          Path(args.memory) / "datasheets_manual"), Path(args.memory) / "part_cards")

    def make(trace: Path) -> Pipeline:
        return Pipeline(llm, lib, memory, cards=cards, scout=True, review=True, trace_dir=trace)

    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "status", "--porcelain", "src"], capture_output=True, text=True).stdout.strip())
    meta = {"name": Path(args.requests).stem, "commit": commit + ("+dirty" if dirty else ""),
            "model": "Ternary Bonsai 27B (PQ2_0)"}
    run_eval(requests, args.runs, args.out, make, lib, meta)
    return 0


def cmd_verdict(args: argparse.Namespace) -> int:
    from .evaluate import set_verdict

    set_verdict(args.dir, args.id, args.run, args.verdict.lower() in ("true", "yes", "1", "ok"), args.note)
    print(Path(args.dir, "summary.md").read_text())
    return 0


def cmd_pending(args: argparse.Namespace) -> int:
    from .memory import DesignMemory, make_embedder

    mem = DesignMemory(args.memory, make_embedder(args.embedder))
    for label, col in (("module", mem.modules), ("block", mem.blocks)):
        got = col.get(where={"origin": "llm_pending"}, include=["metadatas"])
        for i, md in zip(got["ids"], got["metadatas"]):
            print(f"{label} {i[:12]}  {md.get('name') or md.get('instruction')}")
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    """Promote pending LLM results to usable examples. Only run this after YOU reviewed the design."""
    from .memory import DesignMemory, make_embedder

    mem = DesignMemory(args.memory, make_embedder(args.embedder))
    n = 0
    for col in (mem.modules, mem.blocks):
        got = col.get(where={"origin": "llm_pending"}, include=["metadatas"])
        for i, md in zip(got["ids"], got["metadatas"]):
            if any(i.startswith(p) for p in args.ids):
                col.update(ids=[i], metadatas=[{**md, "origin": "llm"}])
                n += 1
    print(f"approved {n} entries")
    return 0


def seed_memory(args: argparse.Namespace) -> int:
    from .memory import DesignMemory, make_embedder

    lib = _lib(args)
    mem = DesignMemory(args.memory, make_embedder(args.embedder))
    mem.index_parts(lib)
    for m in examples.flight_controller().modules:
        mem.add_module(m, lint(m, lib), origin="human")
    print("seeded memory with the reference modules")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pcbc", description=__doc__)
    ap.add_argument("--library", help="parts library JSON (default: built-in seed parts)")
    ap.add_argument("--embedder", choices=["minilm", "hash"], default="minilm", help="text embedder for Chroma")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("lint", help="lint one module JSON")
    p.add_argument("file"); p.add_argument("--strict", action="store_true"); p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_lint)

    p = sub.add_parser("merge", help="merge a design JSON, lint the board, export a KiCad netlist")
    p.add_argument("file"); p.add_argument("--strict", action="store_true"); p.add_argument("-o", "--output")
    p.set_defaults(fn=cmd_merge)

    p = sub.add_parser("render", help="draw a module JSON as a KiCad schematic (+PDF/PNG) and run KiCad ERC")
    p.add_argument("file"); p.add_argument("--out", default="render"); p.set_defaults(fn=cmd_render)

    p = sub.add_parser("example", help="write the reference flight-controller design")
    p.add_argument("--out", default="examples"); p.set_defaults(fn=cmd_example)

    p = sub.add_parser("design", help="run the LLM pipeline against a local llama-server")
    p.add_argument("prompt"); p.add_argument("--name", default="design")
    p.add_argument("--base-url", default="http://127.0.0.1:8080")
    p.add_argument("--memory", default=".pcbc/chroma"); p.add_argument("--out", default="out")
    p.add_argument("--strict", action="store_true"); p.add_argument("--thinking", action="store_true")
    p.add_argument("--kicad", action="store_true", help="use the full installed KiCad symbol library")
    p.add_argument("--no-review", action="store_true", help="skip the LLM reviewer role")
    p.add_argument("--flat", action="store_true", help="design the whole module in one shot instead of block by block")
    p.set_defaults(fn=cmd_design)

    p = sub.add_parser("index-parts", help="(re)index library parts into Chroma")
    p.add_argument("--memory", default=".pcbc/chroma"); p.set_defaults(fn=cmd_index_parts)

    p = sub.add_parser("index-kicad", help="index every installed KiCad symbol into Chroma (for part search)")
    p.add_argument("--memory", default=".pcbc/chroma"); p.set_defaults(fn=cmd_index_kicad)

    p = sub.add_parser("eval", help="run a list of requests several times and summarise (gate pass vs human verdict)")
    p.add_argument("requests"); p.add_argument("--runs", type=int, default=2); p.add_argument("--out", default="evals/run")
    p.add_argument("--memory", default=".pcbc/chroma"); p.add_argument("--base-url", default="http://127.0.0.1:8080")
    p.add_argument("--thinking", action="store_true"); p.set_defaults(fn=cmd_eval)

    p = sub.add_parser("verdict", help="record your judgement of an evaluated drawing (true/false) and rebuild the summary")
    p.add_argument("dir"); p.add_argument("id"); p.add_argument("run", type=int); p.add_argument("verdict")
    p.add_argument("note", nargs="?", default=""); p.set_defaults(fn=cmd_verdict)

    p = sub.add_parser("pending", help="list LLM results waiting for human approval")
    p.add_argument("--memory", default=".pcbc/chroma"); p.set_defaults(fn=cmd_pending)

    p = sub.add_parser("approve", help="approve pending entries by id prefix (after reviewing the design)")
    p.add_argument("ids", nargs="+"); p.add_argument("--memory", default=".pcbc/chroma"); p.set_defaults(fn=cmd_approve)

    p = sub.add_parser("seed-blocks", help="store the generic human-written block patterns in Chroma")
    p.add_argument("--memory", default=".pcbc/chroma"); p.set_defaults(fn=cmd_seed_blocks)

    p = sub.add_parser("seed-memory", help="store the reference modules in Chroma")
    p.add_argument("--memory", default=".pcbc/chroma"); p.set_defaults(fn=seed_memory)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
