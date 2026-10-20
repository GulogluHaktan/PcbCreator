"""Write a module as a real KiCad schematic (.kicad_sch) and render it with kicad-cli.

Symbols are taken verbatim from the installed KiCad symbol libraries, placed on a grid, and every connected pin
gets a net label at its end (KiCad connects a label whose anchor sits on a pin end), so no wires are drawn. The
result opens in KiCad, and kicad-cli turns it into PDF/SVG/PNG and runs KiCad's own ERC on it, which is an
independent check of our netlist.
"""

from __future__ import annotations

import copy
import re
import subprocess
import uuid
from pathlib import Path

from .kicad_import import KICAD_SHARE, QStr, parse_sexpr
from .library import Library
from .lint import Ctx
from .models import Module

_LIB_CACHE: dict[str, dict[str, list]] = {}


def _children(node: list, head: str) -> list[list]:
    return [c for c in node if isinstance(c, list) and c and c[0] == head]


def _lib_nodes(lib: str, share: Path) -> dict[str, list]:
    if lib not in _LIB_CACHE:
        tree = parse_sexpr((share / "symbols" / f"{lib}.kicad_sym").read_text(errors="replace"), keep_quotes=True)
        _LIB_CACHE[lib] = {str(s[1]): s for s in _children(tree[0], "symbol")}
    return _LIB_CACHE[lib]


def flat_symbol(lib: str, name: str, share: Path = KICAD_SHARE) -> list:
    """The symbol with `extends` resolved, as KiCad wants it inside a schematic's lib_symbols."""
    node = _lib_nodes(lib, share)[name]
    ext = _children(node, "extends")
    if not ext:
        return copy.deepcopy(node)
    parent_name = str(ext[0][1])
    new = flat_symbol(lib, parent_name, share)
    new[1] = QStr(name)
    for sub in _children(new, "symbol"):
        sub[1] = QStr(str(sub[1]).replace(parent_name, name, 1))
    for prop in _children(node, "property"):
        for i, old in enumerate(new):
            if isinstance(old, list) and old and old[0] == "property" and old[1] == prop[1]:
                new[i] = copy.deepcopy(prop)
                break
        else:
            new.append(copy.deepcopy(prop))
    return new


def dump(node, depth: int = 0) -> str:
    """Serialise an s-expression tree the way KiCad writes it (tab indented, atoms first, then children)."""
    if isinstance(node, QStr):
        return '"' + str(node).replace("\\", "\\\\").replace('"', '\\"') + '"'
    if not isinstance(node, list):
        return str(node)
    atoms = " ".join(dump(c) for c in node if not isinstance(c, list))
    kids = [c for c in node if isinstance(c, list)]
    if not kids:
        return f"({atoms})"
    pad = "\t" * (depth + 1)
    body = "\n".join(pad + dump(c, depth + 1) for c in kids)
    return f"({atoms}\n{body}\n{chr(9) * depth})"


def _f(x: float) -> str:
    return f"{x:.2f}".rstrip("0").rstrip(".")


def _pins_of(sym: list, name: str) -> dict[int, list[tuple[str, float, float, float]]]:
    """unit -> [(pin number, x, y, angle)] ; unit 0 holds pins shared by all units. Alternate body style skipped."""
    out: dict[int, list] = {}
    for sub in _children(sym, "symbol"):
        m = re.fullmatch(re.escape(name) + r"_(\d+)_(\d+)", str(sub[1]))
        if not m or m.group(2) == "2":
            continue
        for pin in _children(sub, "pin"):
            at = _children(pin, "at")[0]
            number = str(_children(pin, "number")[0][1])
            out.setdefault(int(m.group(1)), []).append((number, float(at[1]), float(at[2]), float(at[3])))
    return out


def _prop(key: str, value: str, x: float, y: float, hide: bool = False) -> str:
    return (f'\t\t(property "{key}" "{value}"\n\t\t\t(at {_f(x)} {_f(y)} 0)\n\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t'
            f'(size 1.27 1.27)\n\t\t\t\t)' + ("\n\t\t\t\t(hide yes)" if hide else "") + "\n\t\t\t)\n\t\t)")


def build_schematic(module: Module, lib: Library, share: Path = KICAD_SHARE) -> str:
    ctx = Ctx(module, lib)
    root = str(uuid.uuid4())
    used: dict[str, list] = {}  # lib_id -> flattened symbol (renamed to lib:name)
    placed: list[str] = []
    x0, y, row_h, x = 30.0, 40.0, 0.0, 30.0
    page_w = 270.0

    for inst in module.parts:
        part = lib.get(inst.part)
        if part is None or ":" not in inst.part:
            raise ValueError(f"{inst.ref}: only parts from the KiCad libraries can be drawn ({inst.part})")
        libname, sname = inst.part.split(":", 1)
        if inst.part not in used:
            sym = flat_symbol(libname, sname, share)
            sym[1] = QStr(inst.part)
            used[inst.part] = sym
        sym = used[inst.part]
        pins = _pins_of(sym, sname)
        units = sorted(u for u in pins if u > 0) or [1]
        for unit in units:
            unit_pins = pins.get(0, []) + pins.get(unit, [])
            xs = [p[1] for p in unit_pins] or [0]
            ys = [p[2] for p in unit_pins] or [0]
            width, height = max(xs) - min(xs) + 52, max(ys) - min(ys) + 22
            if x + width > page_w:
                x, y, row_h = x0, y + row_h, 0.0
            cx = round((x + 26 - min(xs)) / 2.54) * 2.54
            cy = round((y + 12 + max(ys)) / 2.54) * 2.54
            x, row_h = x + width, max(row_h, height)
            pin_blocks, labels = [], []
            nc_numbers = {p.number for p in part.pins if p.type.value == "nc"}
            for number, px, py, ang in unit_pins:
                pin_blocks.append(f'\t\t(pin "{number}"\n\t\t\t(uuid "{uuid.uuid4()}")\n\t\t)')
                net = ctx.pin_net.get((inst.ref, number))
                if net is None:
                    if number in nc_numbers:  # KiCad wants an explicit "no connect" flag on pins left open on purpose
                        labels.append(f'\t(no_connect\n\t\t(at {_f(cx + px)} {_f(cy - py)})\n\t\t(uuid "{uuid.uuid4()}")\n\t)')
                    continue
                lang = int((ang + 180) % 360)
                just = "left bottom" if lang in (0, 90) else "right bottom"
                labels.append(f'\t(label "{net}"\n\t\t(at {_f(cx + px)} {_f(cy - py)} {lang})\n\t\t(effects\n\t\t\t(font\n'
                              f'\t\t\t\t(size 1.27 1.27)\n\t\t\t)\n\t\t\t(justify {just})\n\t\t)\n\t\t(uuid "{uuid.uuid4()}")\n\t)')
            placed.append(
                f'\t(symbol\n\t\t(lib_id "{inst.part}")\n\t\t(at {_f(cx)} {_f(cy)} 0)\n\t\t(unit {unit})\n'
                f'\t\t(exclude_from_sim no)\n\t\t(in_bom yes)\n\t\t(on_board yes)\n\t\t(dnp no)\n\t\t(uuid "{uuid.uuid4()}")\n'
                + _prop("Reference", inst.ref, cx + 3, cy - height / 2 + 2) + "\n"
                + _prop("Value", inst.value or sname, cx + 3, cy - height / 2 + 5) + "\n"
                + _prop("Footprint", part.footprint, cx, cy, True) + "\n"
                + _prop("Datasheet", part.datasheet or "~", cx, cy, True) + "\n"
                + "\n".join(pin_blocks)
                + f'\n\t\t(instances\n\t\t\t(project "{module.name}"\n\t\t\t\t(path "/{root}"\n\t\t\t\t\t(reference "{inst.ref}")\n'
                  f'\t\t\t\t\t(unit {unit})\n\t\t\t\t)\n\t\t\t)\n\t\t)\n\t)\n' + "\n".join(labels))

    lib_block = "\n".join("\t\t" + dump(s, 2) for s in used.values())
    return (f'(kicad_sch\n\t(version 20250114)\n\t(generator "pcbcreator")\n\t(generator_version "0.1")\n'
            f'\t(uuid "{root}")\n\t(paper "A4")\n\t(title_block\n\t\t(title "{module.name}")\n'
            f'\t\t(comment 1 "Generated by pcbcreator from netlist data; check against the datasheets before fabrication")\n'
            f'\t)\n\t(lib_symbols\n{lib_block}\n\t)\n' + "\n".join(placed)
            + '\n\t(sheet_instances\n\t\t(path "/"\n\t\t\t(page "1")\n\t\t)\n\t)\n\t(embedded_fonts no)\n)\n')


def render(module: Module, lib: Library, out_dir: str | Path, stem: str | None = None) -> dict:
    """Write <stem>.kicad_sch, export PDF + PNG, run KiCad's ERC. Returns paths and the ERC summary."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = stem or module.name
    sch = out / f"{stem}.kicad_sch"
    sch.write_text(build_schematic(module, lib))
    result: dict = {"schematic": str(sch)}
    pdf = out / f"{stem}.pdf"
    run = subprocess.run(["kicad-cli", "sch", "export", "pdf", "-o", str(pdf), str(sch)],
                         capture_output=True, text=True, timeout=120, check=False)
    result["pdf_ok"] = run.returncode == 0 and pdf.exists()
    if result["pdf_ok"]:
        png = out / stem
        subprocess.run(["pdftoppm", "-png", "-r", "150", "-singlefile", str(pdf), str(png)],
                       capture_output=True, timeout=120, check=False)
        result["png"] = str(png) + ".png"
    else:
        result["pdf_error"] = (run.stderr or run.stdout)[:400]
    erc = out / f"{stem}.erc.txt"
    subprocess.run(["kicad-cli", "sch", "erc", "--severity-all", "-o", str(erc), str(sch)],
                   capture_output=True, text=True, timeout=120, check=False)
    result["erc"] = erc.read_text() if erc.exists() else "(no ERC report)"
    return result
