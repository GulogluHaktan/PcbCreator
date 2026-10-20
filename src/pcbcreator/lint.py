"""Deterministic design linter. No LLM involved: same input, same findings.

Rule ids: E### = error (blocks the design), W### = warning.
`strict=True` is the production mode: unverified parts become errors (E090).

Rules are plain functions registered with @rule; each takes a Ctx and yields Findings.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Iterable, Literal

from .library import Library
from .models import Module, PartDef, PartPin, PinType, Port

Severity = Literal["error", "warning"]
GROUND_PREFIXES = ("GND", "AGND", "PGND", "DGND", "VSS")
V_TOL = 0.05  # 5 % tolerance when comparing rail voltages


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: Severity
    message: str
    location: str
    hint: str | None = None

    def to_dict(self) -> dict:
        return {"rule": self.rule, "severity": self.severity, "message": self.message,
                "location": self.location, "hint": self.hint}

    def __str__(self) -> str:
        hint = f"  hint: {self.hint}" if self.hint else ""
        return f"{self.severity.upper():7} {self.rule} [{self.location}] {self.message}{hint}"


@dataclass
class Report:
    subject: str
    findings: list[Finding] = field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "warning"]

    @property
    def passed(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        return {"subject": self.subject, "passed": self.passed,
                "errors": len(self.errors), "warnings": len(self.warnings),
                "findings": [f.to_dict() for f in self.findings]}

    def to_text(self) -> str:
        head = f"{self.subject}: {'PASS' if self.passed else 'FAIL'} " \
               f"({len(self.errors)} errors, {len(self.warnings)} warnings)"
        return "\n".join([head, *map(str, self.findings)])


def is_ground_net(name: str) -> bool:
    return name.upper().startswith(GROUND_PREFIXES)


def close(a: float, b: float, tol: float = V_TOL) -> bool:
    return abs(a - b) <= tol * max(abs(a), abs(b), 1e-9)


class Ctx:
    """Resolved view of a module: which pin is on which net, with problems collected."""

    def __init__(self, module: Module, lib: Library, merged: bool = False, strict: bool = False):
        self.module, self.lib, self.merged, self.strict = module, lib, merged, strict
        self.parts: dict[str, PartDef] = {}
        self.pin_net: dict[tuple[str, str], str] = {}
        self.net_pins: dict[str, list[tuple[str, PartPin]]] = defaultdict(list)
        self.ports_by_net: dict[str, list[Port]] = defaultdict(list)
        self.problems: list[Finding] = []
        self._build()

    def _p(self, rule: str, msg: str, loc: str, hint: str | None = None) -> None:
        self.problems.append(Finding(rule, "error", msg, loc, hint))

    def _build(self) -> None:
        m = self.module
        seen_refs: set[str] = set()
        for inst in m.parts:
            if inst.ref in seen_refs:
                self._p("E004", f"duplicate reference designator {inst.ref}", inst.ref)
                continue
            seen_refs.add(inst.ref)
            part = self.lib.get(inst.part)
            if part is None:
                near = self.lib.suggest(inst.part)
                self._p("E001", f"unknown part '{inst.part}'", inst.ref,
                        f"use the full library id; did you mean: {', '.join(near)}?" if near
                        else "use only part ids from the library")
            else:
                self.parts[inst.ref] = part
        seen_nets: set[str] = set()
        for net in m.nets:
            if net.name in seen_nets:
                self._p("E005", f"duplicate net name {net.name}", net.name)
                continue
            seen_nets.add(net.name)
            for node in net.pins:
                ref, dot, token = node.partition(".")
                if not dot or ref not in seen_refs:
                    self._p("E002", f"bad node '{node}' (need REF.PIN with existing REF)", net.name)
                    continue
                part = self.parts.get(ref)
                if part is None:
                    continue  # unknown part already reported
                pin = part.resolve(token)
                if pin is None:
                    self._p("E002", f"{ref} ({part.id}) has no unambiguous pin '{token}'", net.name,
                            f"valid pins: {', '.join(p.number + ':' + p.name for p in part.pins)}")
                    continue
                key = (ref, pin.number)
                if key in self.pin_net and self.pin_net[key] != net.name:
                    self._p("E003", f"pin {ref}.{pin.name} is on two nets "
                            f"({self.pin_net[key]} and {net.name})", f"{ref}.{pin.name}")
                    continue
                if key not in self.pin_net:
                    self.pin_net[key] = net.name
                    self.net_pins[net.name].append((ref, pin))
        for port in m.ports:
            self.ports_by_net[port.net].append(port)

    # --- helpers -------------------------------------------------------
    def net_names(self) -> list[str]:
        return [n.name for n in self.module.nets]

    def net_obj(self, name: str):
        return next(n for n in self.module.nets if n.name == name)

    def pins_of(self, net: str, *types: PinType) -> list[tuple[str, PartPin]]:
        return [(r, p) for r, p in self.net_pins.get(net, []) if p.type in types]

    def has_port(self, net: str, *directions: str) -> bool:
        return any(p.direction in directions for p in self.ports_by_net.get(net, []))

    def rail_voltage(self, net: str) -> float | None:
        declared = self.net_obj(net).voltage
        if declared is not None:
            return declared
        for _, pin in self.pins_of(net, PinType.power_out):
            if pin.output_voltage is not None:
                return pin.output_voltage
        for port in self.ports_by_net.get(net, []):
            if port.voltage is not None:
                return port.voltage
        return None

    def is_powered(self, net: str) -> bool:
        return bool(self.pins_of(net, PinType.power_out)) or self.has_port(net, "power_in")

    def other_terminal_net(self, ref: str, pin: PartPin) -> str | None:
        part = self.parts[ref]
        other = [p for p in part.pins if p.number != pin.number]
        return self.pin_net.get((ref, other[0].number)) if len(other) == 1 else None


RuleFn = Callable[[Ctx], Iterable[Finding]]
RULES: list[RuleFn] = []


def rule(fn: RuleFn) -> RuleFn:
    RULES.append(fn)
    return fn


def _f(rule_id: str, sev: Severity, msg: str, loc: str, hint: str | None = None) -> Finding:
    return Finding(rule_id, sev, msg, loc, hint)


@rule
def structural(ctx: Ctx) -> Iterable[Finding]:
    yield from ctx.problems


@rule
def unconnected_pins(ctx: Ctx) -> Iterable[Finding]:
    for ref, part in ctx.parts.items():
        for pin in part.pins:
            connected = (ref, pin.number) in ctx.pin_net
            if pin.type == PinType.nc and connected:
                yield _f("W011", "warning", f"no-connect pin {ref}.{pin.name} is connected", f"{ref}.{pin.name}")
            elif pin.type != PinType.nc and not connected:
                yield _f("E010", "error", f"pin {ref}.{pin.name} ({pin.type.value}) is unconnected",
                         f"{ref}.{pin.name}", "connect it or choose a part where it is NC")


@rule
def ground_pins(ctx: Ctx) -> Iterable[Finding]:
    for net, nodes in ctx.net_pins.items():
        for ref, pin in nodes:
            if pin.type == PinType.ground and not is_ground_net(net):
                yield _f("E060", "error", f"ground pin {ref}.{pin.name} is on non-ground net {net}",
                         f"{ref}.{pin.name}", "name ground nets GND, AGND, PGND or DGND")
            if pin.type != PinType.ground and is_ground_net(net) and pin.type in (PinType.power_out, PinType.output):
                yield _f("E061", "error", f"{pin.type.value} pin {ref}.{pin.name} drives ground net {net}",
                         f"{ref}.{pin.name}")


@rule
def power_nets(ctx: Ctx) -> Iterable[Finding]:
    for net in ctx.net_names():
        consumers = ctx.pins_of(net, PinType.power_in)
        sources = ctx.pins_of(net, PinType.power_out)
        if consumers and not ctx.is_powered(net):
            yield _f("E020", "error", f"net {net} has power inputs but no source", net,
                     "add a regulator/connector power_out pin or a power_in port")
        if len(sources) > 1:
            yield _f("E022", "error", f"net {net} has {len(sources)} power outputs tied together", net)
        declared = ctx.net_obj(net).voltage
        for ref, pin in sources:
            if declared is not None and pin.output_voltage is not None and not close(declared, pin.output_voltage):
                yield _f("E023", "error", f"net {net} declared {declared} V but {ref}.{pin.name} outputs "
                         f"{pin.output_voltage} V", net)
        rv = ctx.rail_voltage(net)
        for ref, pin in consumers:
            if pin.supply_min is None and pin.supply_max is None:
                continue
            if rv is None:
                yield _f("W022", "warning", f"rail voltage of {net} unknown, cannot check {ref}.{pin.name}", net,
                         "set net.voltage")
            elif (pin.supply_min is not None and rv < pin.supply_min) or \
                 (pin.supply_max is not None and rv > pin.supply_max):
                yield _f("E021", "error", f"{ref}.{pin.name} supply range {pin.supply_min}-{pin.supply_max} V "
                         f"but net {net} is {rv} V", f"{ref}.{pin.name}")


@rule
def signal_drivers(ctx: Ctx) -> Iterable[Finding]:
    drivers = (PinType.output, PinType.bidir, PinType.open_drain, PinType.power_out, PinType.passive)
    for net in ctx.net_names():
        outs = ctx.pins_of(net, PinType.output)
        if len(outs) > 1:
            names = ", ".join(f"{r}.{p.name}" for r, p in outs)
            yield _f("E030", "error", f"net {net} has multiple outputs: {names}", net)
        if outs and ctx.has_port(net, "in"):
            yield _f("E032", "error", f"net {net}: output pin conflicts with incoming port", net)
        ins = ctx.pins_of(net, PinType.input)
        if ins and not (ctx.pins_of(net, *drivers) or ctx.has_port(net, "in", "bidir")):
            names = ", ".join(f"{r}.{p.name}" for r, p in ins)
            yield _f("E031", "error", f"net {net}: input(s) {names} have no possible driver", net)


@rule
def decoupling(ctx: Ctx) -> Iterable[Finding]:
    for ref, part in ctx.parts.items():
        if part.category != "ic":
            continue
        by_net: dict[str, int] = defaultdict(int)
        for pin in part.pins:
            if pin.type == PinType.power_in and (ref, pin.number) in ctx.pin_net:
                by_net[ctx.pin_net[(ref, pin.number)]] += 1
        for net, n_pins in by_net.items():
            caps = 0
            for r, pin in ctx.net_pins[net]:
                p = ctx.parts[r]
                if p.passive_kind == "capacitor":
                    other = ctx.other_terminal_net(r, pin)
                    if other and is_ground_net(other):
                        caps += 1
            if caps == 0:
                yield _f("E040", "error", f"{ref} power pins on {net} have no decoupling capacitor to ground",
                         ref, "add a 100nF capacitor per power pin close to the IC")
            elif caps < n_pins:
                yield _f("W041", "warning", f"{ref} has {n_pins} power pins on {net} but only {caps} "
                         "decoupling capacitor(s)", ref)


@rule
def i2c_pullups(ctx: Ctx) -> Iterable[Finding]:
    for net in ctx.module.nets:
        if net.tag != "i2c":
            continue
        if not ctx.merged and ctx.ports_by_net.get(net.name):
            continue  # the pull-up may live in another module; enforced on the merged board
        ok = False
        for ref, pin in ctx.net_pins.get(net.name, []):
            if ctx.parts[ref].passive_kind == "resistor":
                other = ctx.other_terminal_net(ref, pin)
                if other and not is_ground_net(other) and ctx.rail_voltage(other) is not None:
                    ok = True
        if not ok:
            yield _f("E050", "error", f"I2C net {net.name} has no pull-up resistor to a rail", net.name,
                     "add a 2.2k-10k resistor from the net to the logic rail")


@rule
def ports(ctx: Ctx) -> Iterable[Finding]:
    names = set(ctx.net_names())
    for port in ctx.module.ports:
        if port.net not in names:
            yield _f("E070", "error", f"port refers to missing net {port.net}", port.net)
            continue
        if port.kind == "power":
            if port.direction not in ("power_in", "power_out"):
                yield _f("E073", "error", f"power port {port.net} must be power_in/power_out", port.net)
            if port.voltage is None:
                yield _f("E071", "error", f"power port {port.net} needs a voltage", port.net)
        elif port.direction in ("power_in", "power_out"):
            yield _f("E073", "error", f"{port.kind} port {port.net} cannot be {port.direction}", port.net)
        if port.voltage is not None:
            rv = ctx.rail_voltage(port.net)
            if rv is not None and not close(rv, port.voltage):
                yield _f("E072", "error", f"port {port.net} says {port.voltage} V but net is {rv} V", port.net)


@rule
def led_series_resistor(ctx: Ctx) -> Iterable[Finding]:
    """An LED needs a resistor in SERIES. A resistor merely somewhere on the board does not count: the
    LED must not sit directly between two rails, and a resistor must touch its anode or cathode net."""
    for ref, part in ctx.parts.items():
        if not part.id.startswith(("Device:LED", "LED:")):
            continue
        pins = {p.name: p for p in part.pins}
        if "A" not in pins or "K" not in pins:
            continue  # multi-LED package: not modelled
        a_net, k_net = ctx.pin_net.get((ref, pins["A"].number)), ctx.pin_net.get((ref, pins["K"].number))
        if a_net is None or k_net is None:
            continue  # unconnected pins are reported elsewhere

        def railish(net: str) -> bool:
            return is_ground_net(net) or ctx.rail_voltage(net) is not None or ctx.is_powered(net)

        def has_resistor(net: str) -> bool:
            return any(ctx.parts[r].passive_kind == "resistor" for r, _ in ctx.net_pins.get(net, []))

        if railish(a_net) and railish(k_net):
            yield _f("E051", "error", f"LED {ref} sits directly between {a_net} and {k_net} with no series resistor",
                     ref, "put a resistor between the rail and the LED anode (or cathode and ground), "
                          "not in parallel with the LED")
        elif not (has_resistor(a_net) or has_resistor(k_net)):
            yield _f("E051", "error", f"LED {ref} has no current-limiting resistor on its anode or cathode net",
                     ref, "add a series resistor")


@rule
def connector_return_path(ctx: Ctx) -> Iterable[Finding]:
    """A connector that carries a supply rail must also carry a ground pin: current needs a return path.
    (Found in a real run: two 2-pin connectors wired VIN/VOUT with no ground on either.)"""
    for ref, part in ctx.parts.items():
        if part.category != "connector":
            continue
        nets = [ctx.pin_net[(ref, p.number)] for p in part.pins if (ref, p.number) in ctx.pin_net]
        rails = [n for n in nets if not is_ground_net(n) and (ctx.rail_voltage(n) is not None or ctx.is_powered(n))]
        if rails and not any(is_ground_net(n) for n in nets):
            yield _f("E053", "error", f"connector {ref} carries {', '.join(sorted(set(rails)))} but has no ground pin",
                     ref, "connect one pin of the connector to GND as the return path")


@rule
def dangling_nets(ctx: Ctx) -> Iterable[Finding]:
    for net in ctx.net_names():
        if len(ctx.net_pins.get(net, [])) == 1 and not ctx.ports_by_net.get(net):
            yield _f("W080", "warning", f"net {net} has a single pin and no port", net)


@rule
def unverified_parts(ctx: Ctx) -> Iterable[Finding]:
    for part_id in sorted({p.id for p in ctx.parts.values() if not p.verified}):
        if ctx.strict:
            yield _f("E090", "error", f"part {part_id} is not verified against its datasheet", part_id,
                     "verify the pinout and set verified=true")
        else:
            yield _f("W090", "warning", f"part {part_id} is not verified against its datasheet", part_id)


def lint(module: Module, lib: Library, *, merged: bool = False, strict: bool = False) -> Report:
    ctx = Ctx(module, lib, merged=merged, strict=strict)
    report = Report(module.name)
    for fn in RULES:
        report.findings.extend(fn(ctx))
    return report
