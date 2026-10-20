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
from .lint import Ctx, is_ground_net
from .models import Module

_LIB_CACHE: dict[str, dict[str, list]] = {}
STUB = 5.08  # mm of wire between a pin end and its net label


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
            width, height = max(xs) - min(xs) + 62, max(ys) - min(ys) + 28
            if x + width > page_w:
                x, y, row_h = x0, y + row_h, 0.0
            cx = round((x + 31 - min(xs)) / 2.54) * 2.54
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
                dx, dy = {0: (1, 0), 90: (0, -1), 180: (-1, 0), 270: (0, 1)}[lang]
                x1, y1 = cx + px, cy - py
                x2, y2 = x1 + dx * STUB, y1 + dy * STUB
                labels.append(f'\t(wire\n\t\t(pts\n\t\t\t(xy {_f(x1)} {_f(y1)}) (xy {_f(x2)} {_f(y2)})\n\t\t)\n'
                              f'\t\t(stroke\n\t\t\t(width 0)\n\t\t\t(type default)\n\t\t)\n\t\t(uuid "{uuid.uuid4()}")\n\t)')
                labels.append(f'\t(label "{net}"\n\t\t(at {_f(x2)} {_f(y2)} {lang})\n\t\t(effects\n\t\t\t(font\n'
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


PAPERS = [("A4", 297.0, 210.0), ("A3", 420.0, 297.0), ("A2", 594.0, 420.0), ("A1", 841.0, 594.0)]
OUT_DIR = {0: (1, 0), 90: (0, -1), 180: (-1, 0), 270: (0, 1)}


def _symbol_text(inst, part, sname: str, unit: int, unit_pins, cx: float, cy: float, height: float,
                 root: str, project: str) -> str:
    pin_blocks = "\n".join(f'\t\t(pin "{n}"\n\t\t\t(uuid "{uuid.uuid4()}")\n\t\t)' for n, *_ in unit_pins)
    return (f'\t(symbol\n\t\t(lib_id "{inst.part}")\n\t\t(at {_f(cx)} {_f(cy)} 0)\n\t\t(unit {unit})\n'
            f'\t\t(exclude_from_sim no)\n\t\t(in_bom yes)\n\t\t(on_board yes)\n\t\t(dnp no)\n\t\t(uuid "{uuid.uuid4()}")\n'
            + _prop("Reference", inst.ref, cx + 3, cy - height / 2 + 2) + "\n"
            + _prop("Value", inst.value or sname, cx + 3, cy - height / 2 + 5) + "\n"
            + _prop("Footprint", part.footprint, cx, cy, True) + "\n"
            + _prop("Datasheet", part.datasheet or "~", cx, cy, True) + "\n" + pin_blocks
            + f'\n\t\t(instances\n\t\t\t(project "{project}"\n\t\t\t\t(path "/{root}"\n\t\t\t\t\t(reference "{inst.ref}")\n'
              f'\t\t\t\t\t(unit {unit})\n\t\t\t\t)\n\t\t\t)\n\t\t)\n\t)')


def _wire(x1: float, y1: float, x2: float, y2: float) -> str:
    return (f'\t(wire\n\t\t(pts\n\t\t\t(xy {_f(x1)} {_f(y1)}) (xy {_f(x2)} {_f(y2)})\n\t\t)\n'
            f'\t\t(stroke\n\t\t\t(width 0)\n\t\t\t(type default)\n\t\t)\n\t\t(uuid "{uuid.uuid4()}")\n\t)')


def _label(net: str, x: float, y: float, ang: int) -> str:
    just = "left bottom" if ang in (0, 90) else "right bottom"
    return (f'\t(label "{net}"\n\t\t(at {_f(x)} {_f(y)} {ang})\n\t\t(effects\n\t\t\t(font\n\t\t\t\t(size 1.27 1.27)\n'
            f'\t\t\t)\n\t\t\t(justify {just})\n\t\t)\n\t\t(uuid "{uuid.uuid4()}")\n\t)')


def build_wired(module: Module, lib: Library, share: Path = KICAD_SHARE, variant: int = 0) -> str:
    """Schematic with real wires: parts in columns along the signal flow, one vertical bar per net in the channels
    between columns, every pin wired to its bar. Net labels on the bars keep nets that span several channels
    connected (and make the nets readable). render() verifies the result against KiCad's own netlist."""
    ctx = Ctx(module, lib)
    root = str(uuid.uuid4())
    used: dict[str, list] = {}
    items = []
    for inst in module.parts:
        part = lib.get(inst.part)
        if part is None or ":" not in inst.part:
            raise ValueError(f"{inst.ref}: only parts from the KiCad libraries can be drawn ({inst.part})")
        libname, sname = inst.part.split(":", 1)
        if inst.part not in used:
            sym = flat_symbol(libname, sname, share)
            sym[1] = QStr(inst.part)
            used[inst.part] = sym
        pins = _pins_of(used[inst.part], sname)
        for unit in sorted(u for u in pins if u > 0) or [1]:
            items.append({"inst": inst, "part": part, "sname": sname, "unit": unit,
                          "pins": pins.get(0, []) + pins.get(unit, [])})

    # --- columns: breadth-first along shared non-ground nets, starting at the first connector ----------------
    def nets_of(it) -> set[str]:
        return {ctx.pin_net[(it["inst"].ref, n)] for n, *_ in it["pins"] if (it["inst"].ref, n) in ctx.pin_net}

    signal = [{n for n in nets_of(it) if not is_ground_net(n)} for it in items]
    start = next((i for i, it in enumerate(items) if it["part"].category == "connector"), 0)
    level = {start: 0}
    queue = [start]
    while queue:
        cur = queue.pop(0)
        for j in range(len(items)):
            if j not in level and signal[cur] & signal[j]:
                level[j] = level[cur] + 1
                queue.append(j)
    top = max(level.values(), default=0)
    for j in range(len(items)):
        level.setdefault(j, top + 1)
    ncols = max(level.values()) + 1
    col_items = [[i for i in range(len(items)) if level[i] == c] for c in range(ncols)]

    # --- which channel does each pin use, and which nets run in each channel ---------------------------------
    net_cols: dict[str, list[int]] = {}
    for i, it in enumerate(items):
        for n in nets_of(it):
            net_cols.setdefault(n, []).append(level[i])
    pin_info = []  # (item index, number, px, py, outward angle, net, channel)
    channel_nets: dict[int, list[str]] = {}
    for i, it in enumerate(items):
        for number, px, py, ang in it["pins"]:
            net = ctx.pin_net.get((it["inst"].ref, number))
            if net is None:
                continue
            out = int((ang + 180) % 360)
            c = level[i]
            if out == 0:
                chan = c
            elif out == 180:
                chan = c - 1
            else:
                mean = sum(net_cols[net]) / len(net_cols[net])
                chan = c - 1 if mean < c else c
            pin_info.append((i, number, px, py, out, net, chan))
            channel_nets.setdefault(chan, [])
            if net not in channel_nets[chan]:
                channel_nets[chan].append(net)
    for k in channel_nets:
        channel_nets[k].sort()
        if variant and len(channel_nets[k]) > 1:  # another bar order avoids accidental wire contacts
            r = variant % len(channel_nets[k])
            channel_nets[k] = channel_nets[k][r:] + channel_nets[k][:r]

    # --- geometry --------------------------------------------------------------------------------------------
    def extent(it):
        xs = [p[1] for p in it["pins"]] or [0]
        ys = [p[2] for p in it["pins"]] or [0]
        return min(xs), max(xs), min(ys), max(ys)

    colw = [max((extent(items[i])[1] - extent(items[i])[0] + 2 * STUB for i in col_items[c]), default=10)
            for c in range(ncols)]
    chan_w = {k: 8 + 2.54 * len(channel_nets.get(k, [])) for k in range(-1, ncols)}
    xl, cursor = {}, 12 + chan_w[-1]
    for c in range(ncols):
        xl[c] = cursor
        cursor += colw[c] + chan_w[c]
    total_w = cursor
    pos, total_h = {}, 0.0
    for c in range(ncols):
        y = 28.0
        for i in col_items[c]:
            x_min, _, _, y_max = extent(items[i])
            y_min = extent(items[i])[2]
            cx = round((xl[c] + STUB - x_min) / 2.54) * 2.54
            cy = round((y + STUB + y_max) / 2.54) * 2.54 + 2.54 * ((i * variant) % 4)
            pos[i] = (cx, cy, y_max - y_min + 2 * STUB)
            y += y_max - y_min + 2 * STUB + 14
        total_h = max(total_h, y)
    trunk_x: dict[tuple[int, str], float] = {}
    for k, nets in channel_nets.items():
        x0 = (xl[k] + colw[k]) if k >= 0 else 12.0
        for idx, n in enumerate(nets):
            trunk_x[(k, n)] = round((x0 + 4 + idx * 2.54) / 1.27) * 1.27
    paper, pw, ph = next(((n, w, h) for n, w, h in PAPERS if total_w + 12 <= w and total_h + 50 <= h), PAPERS[-1])

    # --- emit ------------------------------------------------------------------------------------------------
    blocks: list[str] = []
    for i, it in enumerate(items):
        cx, cy, height = pos[i]
        blocks.append(_symbol_text(it["inst"], it["part"], it["sname"], it["unit"], it["pins"], cx, cy, height, root,
                                   module.name))
    attach: dict[tuple[int, str], list[float]] = {}
    nc_flags = []
    for i, number, px, py, out, net, chan in pin_info:
        cx, cy, _ = pos[i]
        x1, y1 = cx + px, cy - py
        tx = trunk_x[(chan, net)]
        if out in (0, 180):
            blocks.append(_wire(x1, y1, tx, y1))
            attach.setdefault((chan, net), []).append(y1)
        else:
            dx, dy = OUT_DIR[out]
            sx, sy = x1 + dx * STUB, y1 + dy * STUB
            blocks.append(_wire(x1, y1, sx, sy))
            blocks.append(_wire(sx, sy, tx, sy))
            attach.setdefault((chan, net), []).append(sy)
    for (chan, net), ys in attach.items():
        tx = trunk_x[(chan, net)]
        lo, hi = min(ys), max(ys)
        if hi > lo:
            blocks.append(_wire(tx, lo, tx, hi))
        for y in set(ys):
            if lo < y < hi:
                blocks.append(f'\t(junction\n\t\t(at {_f(tx)} {_f(y)})\n\t\t(diameter 0)\n\t\t(color 0 0 0 0)\n'
                              f'\t\t(uuid "{uuid.uuid4()}")\n\t)')
        blocks.append(_label(net, tx, lo, 90))
    for i, it in enumerate(items):
        cx, cy, _ = pos[i]
        nc = {p.number for p in it["part"].pins if p.type.value == "nc"}
        for number, px, py, ang in it["pins"]:
            if number in nc and (it["inst"].ref, number) not in ctx.pin_net:
                nc_flags.append(f'\t(no_connect\n\t\t(at {_f(cx + px)} {_f(cy - py)})\n\t\t(uuid "{uuid.uuid4()}")\n\t)')
    lib_block = "\n".join("\t\t" + dump(sym, 2) for sym in used.values())
    return (f'(kicad_sch\n\t(version 20250114)\n\t(generator "pcbcreator")\n\t(generator_version "0.1")\n'
            f'\t(uuid "{root}")\n\t(paper "{paper}")\n\t(title_block\n\t\t(title "{module.name}")\n'
            f'\t\t(comment 1 "Generated by pcbcreator from netlist data; check against the datasheets before fabrication")\n'
            f'\t)\n\t(lib_symbols\n{lib_block}\n\t)\n' + "\n".join(blocks + nc_flags)
            + '\n\t(sheet_instances\n\t\t(path "/"\n\t\t\t(page "1")\n\t\t)\n\t)\n\t(embedded_fonts no)\n)\n')


def kicad_nets(sch: Path) -> dict[str, set[str]]:
    """Net -> {"REF.pinnumber"} as KiCad itself reads the schematic (kicad-cli netlist export)."""
    net_file = sch.with_suffix(".kicad_net_check")
    subprocess.run(["kicad-cli", "sch", "export", "netlist", "--format", "kicadsexpr", "-o", str(net_file), str(sch)],
                   capture_output=True, text=True, timeout=120, check=False)
    text = net_file.read_text() if net_file.exists() else ""
    net_file.unlink(missing_ok=True)
    out: dict[str, set[str]] = {}
    seg = text[text.find("(nets"):] if "(nets" in text else ""
    for m in re.finditer(r'\(net\s+\(code "?\d+"?\)\s+\(name "([^"]+)"\)(.*?)\n\t\t\)', seg, re.S):
        nodes = re.findall(r'\(node\s+\(ref "([^"]+)"\)\s+\(pin "([^"]+)"\)', m.group(2))
        out[m.group(1).lstrip("/")] = {f"{r}.{p}" for r, p in nodes}
    return out


def connectivity_diff(module: Module, lib: Library, sch: Path) -> list[str]:
    """Differences between our netlist and what KiCad reads from the drawing (empty list = identical)."""
    ctx = Ctx(module, lib)
    want: dict[str, set[str]] = {}
    for (ref, number), net in ctx.pin_net.items():
        want.setdefault(net, set()).add(f"{ref}.{number}")
    got = kicad_nets(sch)
    diffs = []
    for net, pins in want.items():
        if got.get(net) != pins:
            diffs.append(f"{net}: ours {sorted(pins)} / KiCad {sorted(got.get(net, set()))}")
    for net, pins in got.items():
        if net not in want and not net.startswith("unconnected-") and pins:
            diffs.append(f"{net}: only in KiCad {sorted(pins)}")
    return diffs


def render(module: Module, lib: Library, out_dir: str | Path, stem: str | None = None) -> dict:
    """Write <stem>.kicad_sch, export PDF + PNG, run KiCad's ERC. Returns paths and the ERC summary."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = stem or module.name
    sch = out / f"{stem}.kicad_sch"
    style = "labels"
    for variant in range(16):  # first drawing whose wiring KiCad reads back as exactly our nets wins
        try:
            sch.write_text(build_wired(module, lib, variant=variant))
            if not connectivity_diff(module, lib, sch):
                style = f"wires (layout variant {variant})"
                break
        except Exception:  # noqa: BLE001
            continue
    if style == "labels":  # nothing verified: the label style is always electrically correct
        sch.write_text(build_schematic(module, lib))
    # KiCad's project manager opens projects, not bare schematics: write a minimal project file next to it
    pro = out / f"{stem}.kicad_pro"
    if not pro.exists():
        pro.write_text('{\n  "meta": {\n    "filename": "%s.kicad_pro",\n    "version": 3\n  }\n}\n' % stem)
    result: dict = {"schematic": str(sch), "style": style}
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
    result["connectivity_diff"] = connectivity_diff(module, lib, sch)
    return result
