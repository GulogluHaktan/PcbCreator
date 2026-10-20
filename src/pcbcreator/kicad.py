"""KiCad export. We write the standard KiCad netlist (.net), which pcbnew imports
with "File > Import > Netlist" to create footprints and ratsnest. Layout is a
separate stage."""

from __future__ import annotations

from datetime import date

from .library import Library
from .models import Module


def _q(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def export_netlist(module: Module, lib: Library) -> str:
    lines = ["(export (version \"E\")",
             f"  (design (source {_q(module.name)}) (date {_q(date.today().isoformat())}) (tool \"pcbcreator\"))",
             "  (components"]
    for inst in module.parts:
        part = lib.get(inst.part)
        if part is None:
            raise ValueError(f"unknown part {inst.part} for {inst.ref}")
        lines.append(f"    (comp (ref {_q(inst.ref)}) (value {_q(inst.value or part.id)}) "
                     f"(footprint {_q(part.footprint)}) (libsource (lib \"pcbcreator\") (part {_q(part.id)})))")
    lines.append("  )")
    lines.append("  (nets")
    for code, net in enumerate(module.nets, start=1):
        lines.append(f"    (net (code {_q(str(code))}) (name {_q(net.name)})")
        for node in net.pins:
            ref, _, tok = node.partition(".")
            inst = next(i for i in module.parts if i.ref == ref)
            part = lib.get(inst.part)
            pin = part.resolve(tok) if part else None
            if pin is None:
                raise ValueError(f"cannot resolve pin {node}")
            lines.append(f"      (node (ref {_q(ref)}) (pin {_q(pin.number)}) (pinfunction {_q(pin.name)}))")
        lines.append("    )")
    lines.append("  )")
    lines.append(")")
    return "\n".join(lines) + "\n"
