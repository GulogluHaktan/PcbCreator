"""Import parts from the official KiCad symbol libraries (.kicad_sym).

Pin numbers, names and electrical types come straight from KiCad's library, so the
LLM never has to invent a pinout. What a symbol does NOT carry is electrical limits
(supply range, regulator output voltage): those stay None until a human confirms them
from the datasheet, and non-trivial parts stay `verified=False` until then.

`KicadLibrary` loads symbol files lazily: `get("Regulator_Linear:AP2112K-3.3")` parses
only that one library file, so the whole 224-library set is searchable but cheap.
"""

from __future__ import annotations

import re
from pathlib import Path

from .library import Library
from .models import PartDef, PartPin, PinType

KICAD_SHARE = Path("/usr/share/kicad")
_TOKEN = re.compile(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()"]+')

_GROUND_NAME = re.compile(r"^(A|D|P|S|E)?(GND|VSS|VEE|0V)\w*$|^GROUND$|^V-$", re.I)
PIN_TYPE = {
    "input": PinType.input, "output": PinType.output, "bidirectional": PinType.bidir,
    "tri_state": PinType.bidir, "passive": PinType.passive, "free": PinType.passive,
    "unspecified": PinType.passive, "power_in": PinType.power_in, "power_out": PinType.power_out,
    "open_collector": PinType.open_drain, "open_emitter": PinType.open_drain,
    "no_connect": PinType.nc,
}
# KiCad symbols for generic passives carry no footprint; pick hand-solderable defaults.
DEFAULT_FOOTPRINT = {
    "resistor": "Resistor_SMD:R_0805_2012Metric",
    "capacitor": "Capacitor_SMD:C_0805_2012Metric",
    "inductor": "Inductor_SMD:L_0805_2012Metric",
    "LED": "LED_SMD:LED_0805_2012Metric",
}


class QStr(str):
    """A string that was quoted in the source (so it is quoted again when written back)."""


def parse_sexpr(text: str, keep_quotes: bool = False) -> list:
    """Parse KiCad s-expressions into nested lists; quoted strings lose their quotes
    (or become QStr when keep_quotes is set, so the tree can be serialised again)."""
    stack: list[list] = [[]]
    for tok in _TOKEN.findall(text):
        if tok == "(":
            stack.append([])
        elif tok == ")":
            done = stack.pop()
            stack[-1].append(done)
        elif tok.startswith('"'):
            val = tok[1:-1].replace('\\"', '"').replace("\\\\", "\\")
            stack[-1].append(QStr(val) if keep_quotes else val)
        else:
            stack[-1].append(tok)
    return stack[0]


def _children(node: list, head: str) -> list[list]:
    return [c for c in node if isinstance(c, list) and c and c[0] == head]


def _props(sym: list) -> dict[str, str]:
    return {p[1]: p[2] for p in _children(sym, "property") if len(p) >= 3}


def _pins(sym: list, out: dict[str, PartPin]) -> None:
    for sub in _children(sym, "symbol"):
        # sub-symbol name is "<name>_<unit>_<bodystyle>"; style 2 is the alternate (De Morgan) body
        if sub[1].endswith("_2") and re.search(r"_\d+_2$", sub[1]):
            continue
        for pin in _children(sub, "pin"):
            name = (_children(pin, "name") or [[0, "?"]])[0][1]
            number = (_children(pin, "number") or [[0, "?"]])[0][1]
            ptype = PIN_TYPE.get(pin[1], PinType.passive)
            if ptype == PinType.power_in and _GROUND_NAME.match(name):
                ptype = PinType.ground  # KiCad types GND pins as power_in; our linter distinguishes them
            out.setdefault(number, PartPin(number=number, name=name, type=ptype))


def _passive_kind(lib: str, name: str) -> str | None:
    if lib != "Device":
        return None
    base = re.split(r"[_]", name)[0]
    return {"R": "resistor", "C": "capacitor", "CP": "capacitor", "L": "inductor"}.get(base)


def parse_symbol_lib(path: Path) -> list[PartDef]:
    lib = path.stem
    tree = parse_sexpr(path.read_text(errors="replace"))
    root = tree[0]
    raw: dict[str, list] = {s[1]: s for s in _children(root, "symbol")}
    parts: list[PartDef] = []
    for name, sym in raw.items():
        pins: dict[str, PartPin] = {}
        props = _props(sym)
        parent = _children(sym, "extends")
        chain = [sym]
        while parent and parent[0][1] in raw:
            psym = raw[parent[0][1]]
            chain.append(psym)
            for k, v in _props(psym).items():
                props.setdefault(k, v)
            parent = _children(psym, "extends")
        for s in reversed(chain):
            _pins(s, pins)
        if not pins:
            continue
        kind = _passive_kind(lib, name)
        category = "passive" if lib in ("Device", "LED") else "connector" if lib.startswith("Connector") else "ic"
        footprint = props.get("Footprint") or DEFAULT_FOOTPRINT.get(kind or ("LED" if name.startswith("LED") else ""), "")
        header = re.fullmatch(r"Conn_01x(\d+)(_.*)?", name) if lib == "Connector_Generic" else None
        if not footprint and header:
            footprint = f"Connector_PinHeader_2.54mm:PinHeader_1x{int(header.group(1)):02d}_P2.54mm_Vertical"
        trivial = category != "ic"  # passives/connectors have no electrical limits to confirm
        parts.append(PartDef(
            id=f"{lib}:{name}", description=" ".join(x for x in (props.get("Description") or props.get("ki_description") or name, props.get("ki_keywords", "")) if x),
            category=category, footprint=footprint,
            pins=sorted(pins.values(), key=lambda p: (not p.number.isdigit(), int(p.number) if p.number.isdigit() else 0, p.number)),
            passive_kind=kind, datasheet=props.get("Datasheet") or None,
            verified=trivial and bool(footprint),
            pinout_source=f"kicad:{lib}:{name}",
        ))
    return parts


class KicadLibrary(Library):
    """Library backed by the installed KiCad symbol libraries, parsed on demand."""

    large = True

    def __init__(self, share: Path = KICAD_SHARE):
        super().__init__([])
        self.sym_dir = share / "symbols"
        self._loaded: set[str] = set()

    def lib_names(self) -> list[str]:
        return sorted(p.stem for p in self.sym_dir.glob("*.kicad_sym") if p.stem != "power")

    def load_lib(self, lib: str) -> None:
        if lib in self._loaded:
            return
        self._loaded.add(lib)
        path = self.sym_dir / f"{lib}.kicad_sym"
        if path.exists():
            for p in parse_symbol_lib(path):
                self.add(p)

    def get(self, part_id: str) -> PartDef | None:
        got = super().get(part_id)
        if got is None and ":" in part_id:
            self.load_lib(part_id.split(":", 1)[0])
            got = super().get(part_id)
        return got

    def load_all(self) -> None:
        for lib in self.lib_names():
            self.load_lib(lib)
