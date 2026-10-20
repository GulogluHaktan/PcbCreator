"""Core data model.

Everything the LLM produces is one of these pydantic models (JSON, never executed
code), so output can be schema-constrained and validated before any linting.

Port direction is from the *module's* point of view:
  in / power_in   -> the signal/power enters the module from outside
  out / power_out -> the module drives it to the outside
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class PinType(str, Enum):
    power_in = "power_in"
    power_out = "power_out"
    ground = "ground"
    input = "input"
    output = "output"
    bidir = "bidir"
    open_drain = "open_drain"
    passive = "passive"
    nc = "nc"


class PartPin(BaseModel):
    number: str
    name: str
    type: PinType
    supply_min: float | None = None  # V, for power_in pins
    supply_max: float | None = None
    output_voltage: float | None = None  # V, for power_out pins


class PartDef(BaseModel):
    id: str
    description: str
    category: Literal["ic", "passive", "connector"]
    footprint: str
    pins: list[PartPin]
    passive_kind: Literal["resistor", "capacitor", "inductor"] | None = None
    # False until the pinout has been checked against the datasheet/KiCad library.
    verified: bool = False
    datasheet: str | None = None
    pinout_source: str | None = None  # e.g. "kicad:Regulator_Linear:AP2112K-3.3"

    def resolve(self, token: str) -> PartPin | None:
        """Resolve a pin by number first, then by unique name."""
        for pin in self.pins:
            if pin.number == token:
                return pin
        named = [p for p in self.pins if p.name == token]
        return named[0] if len(named) == 1 else None


class Instance(BaseModel):
    ref: str = Field(description="Reference designator, e.g. U1, R3, C2")
    part: str = Field(description="Part id from the library")
    value: str | None = Field(default=None, description="e.g. 100nF, 4.7k")


class Net(BaseModel):
    name: str
    pins: list[str] = Field(description='Nodes as "REF.PIN" (pin number or unique name)')
    voltage: float | None = Field(default=None, description="Nominal DC voltage if this is a rail")
    tag: str | None = Field(default=None, description='Bus tag, e.g. "i2c"')


class Port(BaseModel):
    net: str = Field(description="Name of the module net this port exposes; must exist in the module")
    direction: Literal["in", "out", "bidir", "power_in", "power_out"] = Field(
        description="From the module's point of view. Signals: in / out / bidir. Power rails: power_in if the "
                    "module receives the rail, power_out if it provides it. Ground: bidir.")
    kind: Literal["signal", "power", "ground"] = Field(
        default="signal", description="kind=power REQUIRES direction power_in/power_out AND a voltage; "
                                       "kind=ground uses direction bidir; kind=signal never uses power_*")
    voltage: float | None = Field(default=None, description="Nominal volts; required when kind=power")
    protocol: str | None = None
    optional: bool = False


class Module(BaseModel):
    reasoning: str = Field(default="", description=(
        "Think first, in this order: (1) for each required function, which TYPE of part satisfies it and why "
        "(e.g. linear LDO vs switching regulator); (2) the pin NAMES you will wire; (3) every component value with "
        "its arithmetic; (4) then write the netlist."))
    name: str
    description: str = ""
    parts: list[Instance]
    nets: list[Net]
    ports: list[Port] = []


class Link(BaseModel):
    a: str = Field(description='"module.net" of a port')
    b: str = Field(description='"module.net" of a port')


class Design(BaseModel):
    name: str
    modules: list[Module]
    links: list[Link] = []
    expose: list[str] = Field(
        default=[], description='"module.net" ports intentionally left external (board connectors, etc.)'
    )
