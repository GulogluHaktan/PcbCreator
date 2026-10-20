"""Stepwise module construction: the model adds ONE functional block at a time.

Why (literature, see the vault note on stepwise design): hierarchical/stepwise prompting solves what flat
prompting cannot and helps weak models most (ROME); a compact design-state summary carried between steps keeps
prompts short (Yin et al.); pin-level, local feedback is the load-bearing part of a repair loop (PCBSchemaGen).
The steps themselves come from CODE (the requirement checklist), not from a model-invented plan, because models
are unreliable at inventing the decomposition (ROME).

Each step is checked with a LOCAL lint: only findings about the new parts and the nets they touch, and only rule
families that can already be judged (a regulator's decoupling or a net's driver may still come from a later block).
The full lint, coverage check and reviewer run on the assembled module afterwards.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field

from .library import Library
from .lint import Finding, lint
from .models import Instance, Module, Net, Port

# rule families that are meaningful on a partially built module
LOCAL_RULES = {"E001", "E002", "E003", "E004", "E005", "E010", "E030", "E032", "E051", "E053", "E054", "E055", "E060", "E061"}

KIND_ORDER = {"linear_regulator": 0, "switching_regulator": 0, "mcu": 0, "sensor": 1, "led": 2, "connector": 3,
              "support": 8, "other": 9}


class BlockPatch(BaseModel):
    """What the model returns for one block: only the NEW parts and the pins this block adds to nets."""

    reasoning: str = Field(min_length=60, description=(
        "Think first: which parts implement this block, what each pin connects to, and the arithmetic for every "
        "component value (e.g. LED resistor = (supply - LED forward voltage) / current)."))
    parts: list[Instance] = Field(description="New parts only. Use fresh reference designators.")
    nets: list[Net] = Field(description=(
        "Connections made by this block: for each net, the pins ADDED to it. Reuse existing net names (the contract "
        "port nets and nets from earlier blocks); create a new net only for connections internal to this block."))


class RepairPatch(BlockPatch):
    """A minimal edit of a nearly finished module: delete, detach, then add (the model never rewrites everything)."""

    remove_parts: list[str] = Field(default=[], description="Reference designators to delete completely")
    remove_pins: list[str] = Field(default=[], description=(
        "REF.PIN nodes to detach from whatever net they are on. To MOVE a pin: remove it here, then list it under "
        "the right net in 'nets'."))


@dataclass
class Block:
    title: str
    kind: str
    instruction: str


def blocks_from_requirements(req) -> list[Block]:
    """Deterministic decomposition: one block per required part (connectors counted individually)."""
    blocks: list[Block] = []
    for item in sorted(req.required, key=lambda r: KIND_ORDER.get(r.kind, 9)):
        if item.kind in ("other", "support"):  # supporting passives are built inside the block they serve
            continue
        for i in range(item.count):
            suffix = f" ({i + 1} of {item.count})" if item.count > 1 else ""
            extra = {"led": " Include its series current-limiting resistor in this block.",
                     "linear_regulator": " Include its input and output capacitors and tie any enable pin as the "
                                         "datasheet card says in this block."}.get(item.kind, "")
            blocks.append(Block(title=f"{item.kind}{suffix}", kind=item.kind,
                                instruction=f"{item.kind}{suffix}: {item.detail}.{extra}"))
    return blocks


def initial_state(plan, name: str) -> Module:
    """Empty module whose nets and ports are exactly the contract the architect promised."""
    return Module(name=name, description=plan.purpose, parts=[],
                  nets=[Net(name=p.net, pins=[], voltage=p.voltage if p.kind == "power" else None) for p in plan.ports],
                  ports=[p.model_copy() for p in plan.ports])


def apply_patch(state: Module, patch: BlockPatch) -> tuple[Module, list[Finding]]:
    errors: list[Finding] = []
    have = {p.ref for p in state.parts}
    for p in patch.parts:
        if p.ref in have:
            errors.append(Finding("E004", "error", f"reference {p.ref} is already used by an earlier block", p.ref,
                                  "choose a fresh reference designator"))
        have.add(p.ref)
    new = state.model_copy(deep=True)
    new.parts.extend(patch.parts)
    by_name = {n.name: n for n in new.nets}
    for n in patch.nets:
        tgt = by_name.get(n.name)
        if tgt is None:
            tgt = Net(name=n.name, pins=[], voltage=n.voltage, tag=n.tag)
            new.nets.append(tgt)
            by_name[n.name] = tgt
        tgt.pins.extend(x for x in n.pins if x not in tgt.pins)
        tgt.voltage = tgt.voltage if tgt.voltage is not None else n.voltage
        tgt.tag = tgt.tag or n.tag
    return new, errors


def part_kind(part_id: str, lib: Library) -> str:
    """Coarse kind of a library part, reusing the coverage classifier."""
    from .coverage import _matches

    part = lib.get(part_id)
    if part is None:
        return "unknown"
    for kind in ("led", "connector", "linear_regulator", "switching_regulator", "mcu", "sensor"):
        if _matches(kind, part):
            return kind
    return "other"


def scope_findings(patch: BlockPatch, block: Block, lib: Library, owned_kinds: set[str]) -> list[Finding]:
    """A block may only add parts of its own kind (plus supporting passives). An LED does not belong in the
    regulator block: when it was allowed, the model wired it across the rail and later blocks collided with it."""
    out = []
    for inst in patch.parts:
        kind = part_kind(inst.part, lib)
        if kind in owned_kinds and kind != block.kind:
            out.append(Finding("E205", "error",
                               f"{inst.ref} ({kind}) belongs to the '{kind}' block, not to the '{block.kind}' block",
                               inst.ref, "leave it out; it is added in its own step"))
    return out


def apply_repair(state: Module, patch: RepairPatch, lib: Library) -> tuple[Module, list[Finding]]:
    """Delete parts, detach pins (matched by resolved pin number, so U1.EN equals U1.3), then add."""
    errors: list[Finding] = []

    def norm(node: str) -> tuple[str, str]:
        ref, _, tok = node.partition(".")
        inst = next((i for i in state.parts if i.ref == ref), None)
        part = lib.get(inst.part) if inst else None
        pin = part.resolve(tok) if part else None
        return ref, pin.number if pin else tok

    gone = set(patch.remove_parts)
    for ref in gone:
        if ref not in {p.ref for p in state.parts}:
            errors.append(Finding("E002", "error", f"cannot remove {ref}: no such part", ref))
    detach = {norm(n) for n in patch.remove_pins}
    base = state.model_copy(deep=True)
    base.parts = [p for p in base.parts if p.ref not in gone]
    for net in base.nets:
        net.pins = [n for n in net.pins if n.partition(".")[0] not in gone and norm(n) not in detach]
    new, add_errs = apply_patch(base, patch)
    return new, errors + add_errs


def local_findings(state: Module, patch: BlockPatch, lib: Library, cards=None) -> list[Finding]:
    new_refs = {p.ref for p in patch.parts}
    touched = {n.name for n in patch.nets}
    out = []
    for f in lint(state, lib, cards=cards).findings:
        if f.severity != "error" or f.rule not in LOCAL_RULES:
            continue
        if f.location.split(".")[0] in new_refs or f.location in touched:
            out.append(f)
    return out


def state_summary(state: Module, lib: Library) -> str:
    """Compact text of what has been built so far: parts with their pin names, then nets."""
    if not state.parts:
        parts = "(nothing yet)"
    else:
        lines = []
        for inst in state.parts:
            part = lib.get(inst.part)
            pins = ", ".join(p.name or p.number for p in part.pins) if part else "?"
            lines.append(f"{inst.ref} {inst.part} {inst.value or ''} [pins: {pins}]")
        parts = "\n".join(lines)
    nets = "\n".join(f"{n.name}{f' {n.voltage} V' if n.voltage is not None else ''}: "
                     f"{', '.join(n.pins) if n.pins else '(empty)'}" for n in state.nets)
    return f"Parts so far:\n{parts}\n\nNets so far:\n{nets}"


def contract_text(ports: list[Port]) -> str:
    return "\n".join(f"- net {p.net}: {p.kind}/{p.direction}" + (f" {p.voltage} V" if p.voltage is not None else "")
                     for p in ports)
