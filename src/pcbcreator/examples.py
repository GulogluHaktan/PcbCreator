"""Hand-written reference flight-controller design built from 5 modules.

It uses the SIMPLIFIED seed parts, so it passes the linter in normal mode and
fails it in strict mode (E090). It is the regression baseline for linter + merge,
and the first few-shot entries seeded into the design memory.
"""

from __future__ import annotations

from .models import Design, Instance, Link, Module, Net, Port


def _caps(prefix: str, n: int) -> list[Instance]:
    return [Instance(ref=f"{prefix}{i}", part="C", value="100nF") for i in range(1, n + 1)]


def power_module() -> Module:
    return Module(
        name="power",
        description="14.8 V battery input, 5 V buck, 3.3 V LDO",
        parts=[
            Instance(ref="J1", part="CONN_BATT"), Instance(ref="U1", part="BUCK_5V"),
            Instance(ref="U2", part="LDO_3V3"),
            Instance(ref="C1", part="C", value="10uF"), Instance(ref="C2", part="C", value="10uF"),
            Instance(ref="C3", part="C", value="10uF"),
        ],
        nets=[
            Net(name="GND", pins=["J1.2", "U1.2", "U2.2", "C1.2", "C2.2", "C3.2"]),
            Net(name="VBAT", voltage=14.8, pins=["J1.1", "U1.1", "C1.1"]),
            Net(name="+5V", voltage=5.0, pins=["U1.3", "U2.1", "C2.1"]),
            Net(name="+3V3", voltage=3.3, pins=["U2.3", "C3.1"]),
        ],
        ports=[
            Port(net="+5V", direction="power_out", kind="power", voltage=5.0),
            Port(net="+3V3", direction="power_out", kind="power", voltage=3.3),
            Port(net="GND", direction="bidir", kind="ground"),
        ],
    )


def mcu_module() -> Module:
    sig = [("SPI_SCK", 8, "out", "spi"), ("SPI_MOSI", 9, "out", "spi"), ("SPI_MISO", 10, "in", "spi"),
           ("SPI_CS", 11, "out", "spi"), ("IMU_INT", 12, "in", None),
           ("UART_TX", 15, "out", "uart"), ("UART_RX", 16, "in", "uart"),
           ("PWM1", 17, "out", "pwm"), ("PWM2", 18, "out", "pwm"),
           ("PWM3", 19, "out", "pwm"), ("PWM4", 20, "out", "pwm")]
    return Module(
        name="mcu",
        description="Cortex-M flight MCU with reset/boot circuitry and I2C pull-ups",
        parts=[Instance(ref="U1", part="MCU_GENERIC"), *_caps("C", 4),
               Instance(ref="R1", part="R", value="10k"), Instance(ref="R2", part="R", value="10k"),
               Instance(ref="R3", part="R", value="4.7k"), Instance(ref="R4", part="R", value="4.7k")],
        nets=[
            Net(name="+3V3", voltage=3.3, pins=["U1.1", "U1.2", "U1.3", "C1.1", "C2.1", "C3.1", "R1.1", "R3.1", "R4.1"]),
            Net(name="GND", pins=["U1.4", "U1.5", "C1.2", "C2.2", "C3.2", "C4.2", "R2.2"]),
            Net(name="NRST", pins=["U1.6", "R1.2", "C4.1"]),
            Net(name="BOOT0", pins=["U1.7", "R2.1"]),
            Net(name="I2C_SCL", tag="i2c", pins=["U1.13", "R3.2"]),
            Net(name="I2C_SDA", tag="i2c", pins=["U1.14", "R4.2"]),
            *[Net(name=n, pins=[f"U1.{pin}"]) for n, pin, _, _ in sig],
        ],
        ports=[
            Port(net="+3V3", direction="power_in", kind="power", voltage=3.3),
            Port(net="GND", direction="bidir", kind="ground"),
            Port(net="I2C_SCL", direction="bidir", protocol="i2c"),
            Port(net="I2C_SDA", direction="bidir", protocol="i2c"),
            *[Port(net=n, direction=d, protocol=proto) for n, _, d, proto in sig],  # type: ignore[arg-type]
        ],
    )


def imu_module() -> Module:
    return Module(
        name="imu",
        description="SPI 6-axis IMU",
        parts=[Instance(ref="U1", part="IMU_SPI"), Instance(ref="C1", part="C", value="100nF")],
        nets=[
            Net(name="+3V3", voltage=3.3, pins=["U1.1", "C1.1"]),
            Net(name="GND", pins=["U1.2", "C1.2"]),
            Net(name="SPI_SCK", pins=["U1.3"]), Net(name="SPI_MOSI", pins=["U1.4"]),
            Net(name="SPI_MISO", pins=["U1.5"]), Net(name="SPI_CS", pins=["U1.6"]),
            Net(name="IMU_INT", pins=["U1.7"]),
        ],
        ports=[
            Port(net="+3V3", direction="power_in", kind="power", voltage=3.3),
            Port(net="GND", direction="bidir", kind="ground"),
            Port(net="SPI_SCK", direction="in", protocol="spi"), Port(net="SPI_MOSI", direction="in", protocol="spi"),
            Port(net="SPI_MISO", direction="out", protocol="spi"), Port(net="SPI_CS", direction="in", protocol="spi"),
            Port(net="IMU_INT", direction="out"),
        ],
    )


def baro_module() -> Module:
    return Module(
        name="baro",
        description="I2C barometer",
        parts=[Instance(ref="U1", part="BARO_I2C"), Instance(ref="C1", part="C", value="100nF")],
        nets=[
            Net(name="+3V3", voltage=3.3, pins=["U1.1", "C1.1"]),
            Net(name="GND", pins=["U1.2", "C1.2"]),
            Net(name="I2C_SCL", tag="i2c", pins=["U1.3"]), Net(name="I2C_SDA", tag="i2c", pins=["U1.4"]),
        ],
        ports=[
            Port(net="+3V3", direction="power_in", kind="power", voltage=3.3),
            Port(net="GND", direction="bidir", kind="ground"),
            Port(net="I2C_SCL", direction="bidir", protocol="i2c"),
            Port(net="I2C_SDA", direction="bidir", protocol="i2c"),
        ],
    )


def io_module() -> Module:
    return Module(
        name="io",
        description="ESC outputs and receiver connector",
        parts=[Instance(ref="J1", part="CONN_ESC4"), Instance(ref="J2", part="CONN_RX")],
        nets=[
            Net(name="GND", pins=["J1.5", "J2.4"]),
            Net(name="+5V", voltage=5.0, pins=["J2.3"]),
            Net(name="PWM1", pins=["J1.1"]), Net(name="PWM2", pins=["J1.2"]),
            Net(name="PWM3", pins=["J1.3"]), Net(name="PWM4", pins=["J1.4"]),
            Net(name="UART_TX", pins=["J2.2"]), Net(name="UART_RX", pins=["J2.1"]),
        ],
        ports=[
            Port(net="+5V", direction="power_in", kind="power", voltage=5.0),
            Port(net="GND", direction="bidir", kind="ground"),
            *[Port(net=f"PWM{i}", direction="in", protocol="pwm") for i in range(1, 5)],
            Port(net="UART_TX", direction="in", protocol="uart"),
            Port(net="UART_RX", direction="out", protocol="uart"),
        ],
    )


def flight_controller() -> Design:
    links = [Link(a="power.+5V", b="io.+5V")]
    links += [Link(a="power.+3V3", b=f"{m}.+3V3") for m in ("mcu", "imu", "baro")]
    for n in ("SPI_SCK", "SPI_MOSI", "SPI_MISO", "SPI_CS", "IMU_INT"):
        links.append(Link(a=f"mcu.{n}", b=f"imu.{n}"))
    links += [Link(a="mcu.I2C_SCL", b="baro.I2C_SCL"), Link(a="mcu.I2C_SDA", b="baro.I2C_SDA")]
    links += [Link(a=f"mcu.PWM{i}", b=f"io.PWM{i}") for i in range(1, 5)]
    links += [Link(a="mcu.UART_TX", b="io.UART_TX"), Link(a="io.UART_RX", b="mcu.UART_RX")]
    return Design(name="flight_controller",
                  modules=[power_module(), mcu_module(), imu_module(), baro_module(), io_module()],
                  links=links)
