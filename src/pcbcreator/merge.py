"""Merge independently designed modules into one board through their port contracts.

The merge is deterministic code, not an LLM step. It enforces the contract between
modules (kind, direction, voltage, protocol, single driver) before the merged board
is linted as a whole, which is where cross-module faults show up (e.g. a missing
I2C pull-up that no single module could see).
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from .library import Library
from .lint import Finding, close, lint
from .models import Design, Instance, Module, Net, Port


@dataclass
class MergeResult:
    module: Module | None
    findings: list[Finding] = field(default_factory=list)
    ref_map: dict[tuple[str, str], str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.module is not None and not any(f.severity == "error" for f in self.findings)


def _e(rule: str, msg: str, loc: str, hint: str | None = None) -> Finding:
    return Finding(rule, "error", msg, loc, hint)


def check_contract(module: Module, expected: list[Port]) -> list[Finding]:
    """Check a designed module against the ports the architect promised for it."""
    out: list[Finding] = []
    have = {p.net: p for p in module.ports}
    for exp in expected:
        got = have.get(exp.net)
        loc = f"{module.name}.{exp.net}"
        if got is None:
            out.append(_e("E110", f"module is missing contract port {exp.net}", loc,
                          f"add a {exp.direction} port on net {exp.net}"))
            continue
        if got.direction != exp.direction or got.kind != exp.kind:
            out.append(_e("E111", f"port {exp.net} is {got.kind}/{got.direction}, contract says "
                          f"{exp.kind}/{exp.direction}", loc))
        if exp.voltage is not None and got.voltage is not None and not close(exp.voltage, got.voltage):
            out.append(_e("E112", f"port {exp.net} is {got.voltage} V, contract says {exp.voltage} V", loc))
    for extra in set(have) - {p.net for p in expected}:
        out.append(Finding("W113", "warning", f"port {extra} is not in the contract", f"{module.name}.{extra}"))
    return out


class _UF:
    def __init__(self) -> None:
        self.p: dict[tuple[str, str], tuple[str, str]] = {}

    def find(self, x: tuple[str, str]) -> tuple[str, str]:
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: tuple[str, str], b: tuple[str, str]) -> None:
        self.p[self.find(a)] = self.find(b)


def _endpoint(text: str, mods: dict[str, Module], findings: list[Finding]) -> tuple[str, str] | None:
    mod, dot, net = text.partition(".")
    if not dot or mod not in mods:
        findings.append(_e("E100", f"bad link endpoint '{text}' (need module.net of an existing module)", text))
        return None
    if net not in {p.net for p in mods[mod].ports}:
        findings.append(_e("E100", f"{text} is not a port of module {mod}", text,
                           f"ports: {', '.join(p.net for p in mods[mod].ports)}"))
        return None
    return mod, net


def merge(design: Design, lib: Library, *, strict: bool = False) -> MergeResult:
    findings: list[Finding] = []
    mods: dict[str, Module] = {}
    for m in design.modules:
        if m.name in mods:
            findings.append(_e("E100", f"duplicate module name {m.name}", m.name))
        mods[m.name] = m

    uf = _UF()
    for link in design.links:
        a, b = _endpoint(link.a, mods, findings), _endpoint(link.b, mods, findings)
        if a and b:
            uf.union(a, b)
    exposed: set[tuple[str, str]] = set()
    for text in design.expose:
        ep = _endpoint(text, mods, findings)
        if ep:
            exposed.add(ep)
    if any(f.rule == "E100" for f in findings):
        return MergeResult(None, findings)

    # --- port groups and contract checks ---------------------------------
    port_of: dict[tuple[str, str], list[Port]] = defaultdict(list)
    for m in design.modules:
        for p in m.ports:
            port_of[(m.name, p.net)].append(p)
    groups: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    for key in port_of:
        groups[uf.find(key)].append(key)

    for root, members in groups.items():
        plist = [p for k in members for p in port_of[k]]
        loc = ", ".join(f"{m}.{n}" for m, n in members)
        if all(p.kind == "ground" for p in plist):
            continue  # all grounds are tied to the global GND
        is_exposed = any(k in exposed for k in members)
        if len({p.kind for p in plist}) > 1:
            findings.append(_e("E101", f"linked ports mix kinds {sorted({p.kind for p in plist})}", loc))
        protos = {p.protocol for p in plist if p.protocol}
        if len(protos) > 1:
            findings.append(_e("E104", f"linked ports mix protocols {sorted(protos)}", loc))
        volts = [p.voltage for p in plist if p.voltage is not None]
        if volts and not all(close(volts[0], v) for v in volts):
            findings.append(_e("E103", f"linked ports disagree on voltage {sorted(set(volts))}", loc))
        outs = sum(p.direction in ("out", "power_out") for p in plist)
        ins = sum(p.direction in ("in", "power_in") for p in plist)
        bidirs = sum(p.direction == "bidir" for p in plist)
        if outs > 1:
            findings.append(_e("E105", f"{outs} drivers on one linked net", loc))
        if ins and outs + bidirs == 0 and not is_exposed:
            findings.append(_e("E106", "linked net has consumers but no driver", loc,
                               "link it to the module that drives it or add it to design.expose"))
        if len(members) == 1 and not is_exposed and not all(p.optional for p in plist):
            findings.append(_e("E107", f"port {loc} is not linked and not exposed", loc,
                               "add a link, add it to design.expose, or mark the port optional"))
        if outs and not ins and not bidirs and len(members) > 1 and not is_exposed:
            findings.append(Finding("W108", "warning", "driven net has no consumer", loc))

    # --- net naming ------------------------------------------------------
    def key_of(mod: str, net: str) -> tuple[str, str]:
        return uf.find((mod, net)) if (mod, net) in port_of else (mod, net)

    def is_global_ground(net: str) -> bool:
        return net.upper().startswith("GND")

    members_of: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    for m in design.modules:
        for n in m.nets:
            members_of[key_of(m.name, n.name)].append((m.name, n.name))

    def preferred(key: tuple[str, str]) -> tuple[str, str]:
        mem = members_of[key]
        if all(is_global_ground(n) for _, n in mem):
            return "", "GND"
        for mod, net in mem:  # the driving side names the net
            if any(p.direction in ("out", "power_out") for p in port_of.get((mod, net), [])):
                return mod, net
        return mem[0]

    pref = {k: preferred(k) for k in members_of}
    name_count: dict[str, int] = defaultdict(int)
    for _, net in pref.values():
        name_count[net] += 1
    final: dict[tuple[str, str], str] = {}
    for k, (mod, net) in pref.items():
        final[k] = "GND" if net == "GND" and not mod else (f"{mod}/{net}" if name_count[net] > 1 else net)

    # --- refs ------------------------------------------------------------
    counters: dict[str, int] = defaultdict(int)
    ref_map: dict[tuple[str, str], str] = {}
    parts: list[Instance] = []
    for m in design.modules:
        for inst in m.parts:
            prefix = re.match(r"[A-Za-z]+", inst.ref)
            letters = prefix.group(0) if prefix else "X"
            counters[letters] += 1
            new = f"{letters}{counters[letters]}"
            ref_map[(m.name, inst.ref)] = new
            parts.append(Instance(ref=new, part=inst.part, value=inst.value))

    # --- nets ------------------------------------------------------------
    merged_nets: dict[str, Net] = {}
    for m in design.modules:
        for n in m.nets:
            name = final[key_of(m.name, n.name)]
            pins = []
            for node in n.pins:
                ref, dot, tok = node.partition(".")
                pins.append(f"{ref_map.get((m.name, ref), ref)}.{tok}")
            tgt = merged_nets.setdefault(name, Net(name=name, pins=[], voltage=None, tag=None))
            tgt.pins.extend(x for x in pins if x not in tgt.pins)
            if n.voltage is not None:
                if tgt.voltage is not None and not close(tgt.voltage, n.voltage):
                    findings.append(_e("E103", f"net {name} declared {tgt.voltage} V and {n.voltage} V", name))
                tgt.voltage = tgt.voltage if tgt.voltage is not None else n.voltage
            tgt.tag = tgt.tag or n.tag

    ports: list[Port] = []
    for mod, net in sorted(exposed):
        for p in port_of[(mod, net)]:
            ports.append(p.model_copy(update={"net": final[key_of(mod, net)]}))

    merged = Module(name=design.name, description=f"merged from {', '.join(mods)}",
                    parts=parts, nets=list(merged_nets.values()), ports=ports)
    if any(f.severity == "error" for f in findings):
        return MergeResult(merged, findings, ref_map)
    findings.extend(lint(merged, lib, merged=True, strict=strict).findings)
    return MergeResult(merged, findings, ref_map)
