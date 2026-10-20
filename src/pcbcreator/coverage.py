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


_MENTIONS = [  # (pattern in the request, checklist kinds that satisfy it, label)
    (r"\bleds?\b|light[- ]emitting", ("led",), "an LED"),
    (r"regulator|\bldo\b|low[- ]dropout|buck|boost", ("linear_regulator", "switching_regulator"), "a regulator"),
    (r"sensor|accelerometer|barometer|barometric|gyro|\bimu\b|magnetometer", ("sensor",), "a sensor"),
    (r"microcontroller|\bmcu\b", ("mcu",), "an MCU"),
]


def request_checklist_findings(request: str, req) -> list[Finding]:
    """Cross-check the model's checklist against the words of the request itself. The model sometimes returns a
    checklist that silently drops parts (a real run listed only the connector of an LED board); the later gates then
    only verify what the checklist asks for, so an incomplete list would pass unnoticed."""
    import re

    text = request.lower()
    out: list[Finding] = []
    have = {}
    for item in req.required:
        have[item.kind] = have.get(item.kind, 0) + item.count
    for pattern, kinds, label in _MENTIONS:
        if re.search(pattern, text) and not any(have.get(k) for k in kinds):
            out.append(Finding("E201", "error", f"the request mentions {label} but the checklist has no "
                               f"'{kinds[0]}' item", "requirements", f"add a '{kinds[0]}' item to 'required'"))
    wanted = len(re.findall(r"\d+[- ]pin\s+(?:connector|header)|\b(?:connector|header)s?\b", text))
    pins = len(re.findall(r"\d+[- ]pin", text))
    need = max(pins, 1) if re.search(r"connector|header", text) else 0
    del wanted
    if need and have.get("connector", 0) < need:
        out.append(Finding("E201", "error", f"the request names {need} connector(s)/header(s) but the checklist counts "
                           f"{have.get('connector', 0)}", "requirements", "list every connector, one per N-pin mention"))
    return out
