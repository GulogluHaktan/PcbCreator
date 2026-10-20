"""Generic, human-written connection patterns (origin="human"), stored in the block memory.

These are textbook patterns at block level (an LED with its series resistor, an IC power pin with its
decoupling capacitor, I2C pull-ups, a linear-regulator stage), not finished boards. Worked numbers deliberately
differ from the numbers of any evaluation request, and every pattern says that values must be recomputed.
Net names are examples: the model must use the contract nets of its own module.
"""

from __future__ import annotations

from .models import Instance, Net
from .stepwise import BlockPatch

NOTE = (" Net names and values here are an EXAMPLE: use the contract nets of your module and recompute every value "
        "from your own rail voltage, part datasheet and target current.")


def patterns() -> list[tuple[str, str, BlockPatch]]:
    return [
        ("led", "LED power indicator on a rail with a series current-limiting resistor",
         BlockPatch(
             reasoning=("Current path: rail -> R1 -> LED anode -> LED cathode -> GND, so R1 is in SERIES with the LED. "
                        "R = (Vrail - Vf) / I. Example numbers: rail 5 V, Vf 2.1 V, I = 10 mA gives 290 ohm, rounded "
                        "to the E24 value 300 ohm." + NOTE),
             parts=[Instance(ref="R1", part="Device:R", value="300"), Instance(ref="D1", part="Device:LED", value="red")],
             nets=[Net(name="RAIL", pins=["R1.1"]), Net(name="LED_A", pins=["R1.2", "D1.A"]),
                   Net(name="GND", pins=["D1.K"])])),
        ("decoupling", "Decoupling capacitor for an IC power pin, placed between the rail and ground",
         BlockPatch(
             reasoning=("Every IC supply pin needs a 100 nF ceramic capacitor between that pin's rail and GND, one "
                        "capacitor per supply pin, as close to the pin as possible. C1 goes from the rail net that "
                        "feeds the pin to GND." + NOTE),
             parts=[Instance(ref="C1", part="Device:C", value="100nF")],
             nets=[Net(name="RAIL", pins=["C1.1"]), Net(name="GND", pins=["C1.2"])])),
        ("i2c_pullups", "I2C bus pull-up resistors from SDA and SCL to the logic rail",
         BlockPatch(
             reasoning=("I2C lines are open-drain, so each of SDA and SCL needs a pull-up resistor to the logic rail, "
                        "typically 2.2k to 10k; one pair per bus, not per device. Tag both nets i2c so the linter "
                        "can verify the pull-ups." + NOTE),
             parts=[Instance(ref="R1", part="Device:R", value="4.7k"), Instance(ref="R2", part="Device:R", value="4.7k")],
             nets=[Net(name="RAIL", pins=["R1.1", "R2.1"]), Net(name="SDA", tag="i2c", pins=["R1.2"]),
                   Net(name="SCL", tag="i2c", pins=["R2.2"])])),
        ("linear_regulator", "Fixed-output linear regulator stage with input and output capacitors and enable tied on",
         BlockPatch(
             reasoning=("A linear regulator needs a capacitor on its input and on its output, both to GND; the "
                        "required value and type come from the part's datasheet card, not from this example. An "
                        "enable pin that must be active is tied to the input rail. Unused NC pins stay open." + NOTE),
             parts=[Instance(ref="U1", part="Regulator_Linear:AP2112K-3.3"), Instance(ref="C1", part="Device:C", value="1uF"),
                    Instance(ref="C2", part="Device:C", value="1uF")],
             nets=[Net(name="IN_RAIL", pins=["U1.VIN", "U1.EN", "C1.1"]),
                   Net(name="GND", pins=["U1.GND", "C1.2", "C2.2"]), Net(name="OUT_RAIL", pins=["U1.VOUT", "C2.1"])])),
    ]
