"""Requirement coverage: does the finished module actually contain what was asked for?

Deterministic and deliberately coarse: it counts parts by kind, using the KiCad library
naming (Regulator_Linear vs Regulator_Switching, LED, MCU_...). The linter checks that a
design is electrically consistent; this checks that it is the design that was requested.
"""

from __future__ import annotations

from .library import Library
from .lint import Finding
from .models import Module, PartDef


def _matches(kind: str, part: PartDef) -> bool:
    pid = part.id
    if kind == "connector":
        return part.category == "connector"
    if kind == "linear_regulator":
        return pid.startswith("Regulator_Linear:")
    if kind == "switching_regulator":
        return pid.startswith(("Regulator_Switching:", "Regulator_SwitchedCapacitor:"))
    if kind == "led":
        return pid.startswith(("Device:LED", "LED:"))
    if kind == "mcu":
        return pid.startswith("MCU_")
    if kind == "sensor":
        return pid.startswith("Sensor")
    return True  # "other" cannot be checked deterministically


def check_coverage(module: Module, lib: Library, required) -> list[Finding]:
    out: list[Finding] = []
    for item in required:
        if item.kind in ("other", "support"):
            continue
        have = sum(1 for inst in module.parts
                   if (p := lib.get(inst.part)) is not None and _matches(item.kind, p))
        if have < item.count:
            out.append(Finding("E200", "error",
                               f"requirement not met: needs {item.count} x {item.kind} ({item.detail}), design has {have}",
                               module.name, "add or replace parts so the design contains what was requested"))
    return out
