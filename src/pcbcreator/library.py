"""Part library.

The seed parts below are SIMPLIFIED, GENERIC symbols used to develop and test the
pipeline. They are all `verified=False`: `lint(strict=True)` (the production mode)
rejects any design that uses them. Real parts must be imported with their pinouts
checked against the datasheet / official KiCad symbol library and marked verified.
"""

from __future__ import annotations

import difflib
import json
from pathlib import Path

from .models import PartDef, PartPin, PinType

T = PinType


def _pin(number: str, name: str, type_: PinType, **kw: float) -> PartPin:
    return PartPin(number=number, name=name, type=type_, **kw)


def _passive(id_: str, desc: str, kind: str, footprint: str) -> PartDef:
    return PartDef(
        id=id_,
        description=desc,
        category="passive",
        footprint=footprint,
        passive_kind=kind,  # type: ignore[arg-type]
        pins=[_pin("1", "1", T.passive), _pin("2", "2", T.passive)],
        verified=True,  # two-terminal passives have no pinout to get wrong
    )


def seed_parts() -> list[PartDef]:
    return [
        _passive("R", "Generic resistor", "resistor", "Resistor_SMD:R_0402_1005Metric"),
        _passive("C", "Generic capacitor", "capacitor", "Capacitor_SMD:C_0402_1005Metric"),
        PartDef(
            id="CONN_BATT", description="Battery input connector (nominal 4S, 14.8 V)",
            category="connector", footprint="Connector_JST:JST_XH_B2B-XH-A_1x02_P2.50mm_Vertical",
            pins=[_pin("1", "BAT+", T.power_out, output_voltage=14.8), _pin("2", "BAT-", T.ground)],
        ),
        PartDef(
            id="BUCK_5V", description="Generic 5 V buck regulator, 6-30 V input",
            category="ic", footprint="Package_TO_SOT_SMD:SOT-23-6",
            pins=[_pin("1", "VIN", T.power_in, supply_min=6.0, supply_max=30.0),
                  _pin("2", "GND", T.ground),
                  _pin("3", "VOUT", T.power_out, output_voltage=5.0)],
        ),
        PartDef(
            id="LDO_3V3", description="Generic 3.3 V LDO, 3.5-12 V input",
            category="ic", footprint="Package_TO_SOT_SMD:SOT-23-5",
            pins=[_pin("1", "VIN", T.power_in, supply_min=3.5, supply_max=12.0),
                  _pin("2", "GND", T.ground),
                  _pin("3", "VOUT", T.power_out, output_voltage=3.3)],
        ),
        PartDef(
            id="MCU_GENERIC", description="Generic Cortex-M MCU (simplified), 1.71-3.6 V",
            category="ic", footprint="Package_QFP:LQFP-64_10x10mm_P0.5mm",
            pins=[
                _pin("1", "VDD", T.power_in, supply_min=1.71, supply_max=3.6),
                _pin("2", "VDD2", T.power_in, supply_min=1.71, supply_max=3.6),
                _pin("3", "VDDA", T.power_in, supply_min=1.71, supply_max=3.6),
                _pin("4", "VSS", T.ground), _pin("5", "VSSA", T.ground),
                _pin("6", "NRST", T.input), _pin("7", "BOOT0", T.input),
                _pin("8", "SPI1_SCK", T.output), _pin("9", "SPI1_MOSI", T.output),
                _pin("10", "SPI1_MISO", T.input), _pin("11", "SPI1_CS", T.output),
                _pin("12", "IMU_INT", T.input),
                _pin("13", "I2C1_SCL", T.open_drain), _pin("14", "I2C1_SDA", T.open_drain),
                _pin("15", "UART_TX", T.output), _pin("16", "UART_RX", T.input),
                _pin("17", "PWM1", T.output), _pin("18", "PWM2", T.output),
                _pin("19", "PWM3", T.output), _pin("20", "PWM4", T.output),
            ],
        ),
        PartDef(
            id="IMU_SPI", description="Generic 6-axis IMU, SPI, 1.71-3.6 V",
            category="ic", footprint="Package_LGA:LGA-14_3x2.5mm_P0.5mm",
            pins=[_pin("1", "VDD", T.power_in, supply_min=1.71, supply_max=3.6),
                  _pin("2", "GND", T.ground),
                  _pin("3", "SCK", T.input), _pin("4", "SDI", T.input),
                  _pin("5", "SDO", T.output), _pin("6", "CS", T.input),
                  _pin("7", "INT", T.output)],
        ),
        PartDef(
            id="BARO_I2C", description="Generic barometer, I2C, 1.71-3.6 V",
            category="ic", footprint="Package_LGA:Bosch_LGA-8_2.5x2.5mm_P0.65mm",
            pins=[_pin("1", "VDD", T.power_in, supply_min=1.71, supply_max=3.6),
                  _pin("2", "GND", T.ground),
                  _pin("3", "SCL", T.input), _pin("4", "SDA", T.bidir)],
        ),
        PartDef(
            id="CONN_ESC4", description="4-channel ESC signal header + ground",
            category="connector", footprint="Connector_PinHeader_2.54mm:PinHeader_1x05_P2.54mm_Vertical",
            pins=[_pin("1", "SIG1", T.passive), _pin("2", "SIG2", T.passive),
                  _pin("3", "SIG3", T.passive), _pin("4", "SIG4", T.passive),
                  _pin("5", "GND", T.ground)],
        ),
        PartDef(
            id="CONN_RX", description="Receiver connector: UART + 5 V",
            category="connector", footprint="Connector_JST:JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal",
            pins=[_pin("1", "TX", T.passive), _pin("2", "RX", T.passive),
                  _pin("3", "5V", T.power_in, supply_min=4.5, supply_max=5.5),
                  _pin("4", "GND", T.ground)],
        ),
    ]


class Library:
    large = False  # True for lazily loaded libraries whose all() is not the whole catalogue

    def __init__(self, parts: list[PartDef] | None = None):
        self._parts: dict[str, PartDef] = {}
        for p in parts if parts is not None else seed_parts():
            self.add(p)

    def add(self, part: PartDef) -> None:
        self._parts[part.id] = part

    def get(self, part_id: str) -> PartDef | None:
        return self._parts.get(part_id)

    def suggest(self, part_id: str, n: int = 3) -> list[str]:
        """Closest known part ids, for linter hints (e.g. 'C' -> 'Device:C')."""
        ids = list(self._parts)
        exact = [i for i in ids if i.split(":", 1)[-1].lower() == part_id.lower()]
        return exact[:n] or difflib.get_close_matches(part_id, ids, n=n, cutoff=0.5)

    def all(self) -> list[PartDef]:
        return list(self._parts.values())

    @classmethod
    def from_json(cls, path: str | Path) -> "Library":
        data = json.loads(Path(path).read_text())
        return cls([PartDef.model_validate(p) for p in data])

    def to_json(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps([p.model_dump(mode="json") for p in self.all()], indent=2))

    def summary(self, part: PartDef) -> str:
        """Compact one-line description for LLM prompts."""
        shown = part.pins[:24]
        pins = ", ".join(f"{p.number}:{p.name}({p.type.value})" for p in shown)
        more = f", ... (+{len(part.pins) - 24} more pins)" if len(part.pins) > 24 else ""
        return f"{part.id} [{part.category}] {part.description} | footprint {part.footprint} | pins: {pins}{more}"
