"""Human-readable text view of a module: parts with what they are, and every net with named pins."""

from __future__ import annotations

from .library import Library
from .models import Module


def describe_module(module: Module, lib: Library) -> str:
    lines = [f"MODULE {module.name}: {module.description}", "", "PARTS"]
    for inst in module.parts:
        part = lib.get(inst.part)
        what = (part.description[:90] if part else "UNKNOWN PART")
        pins = ""
        if part and part.category == "ic":  # datasheets cover several package variants: show what THIS part has
            pins = "  [pins: " + ", ".join(p.name or p.number for p in part.pins) + "]"
        lines.append(f"  {inst.ref:<5} {inst.part:<42} {inst.value or '':<8} {what}{pins}")
    lines += ["", "NETS (pin names in brackets)"]
    for net in module.nets:
        pins = []
        for node in net.pins:
            ref, _, tok = node.partition(".")
            inst = next((i for i in module.parts if i.ref == ref), None)
            part = lib.get(inst.part) if inst else None
            pin = part.resolve(tok) if part else None
            pins.append(f"{ref}.{tok}" + (f"[{pin.name}]" if pin and pin.name else ""))
        extra = (f"  {net.voltage} V" if net.voltage is not None else "") + (f"  tag={net.tag}" if net.tag else "")
        lines.append(f"  {net.name:<12}{extra}: " + ", ".join(pins))
    lines += ["", "PORTS"]
    for p in module.ports:
        lines.append(f"  {p.net:<12} {p.kind}/{p.direction}" + (f" {p.voltage} V" if p.voltage is not None else ""))
    return "\n".join(lines)
