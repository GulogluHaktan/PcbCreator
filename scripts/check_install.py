"""Check that the toolchain pcbcreator needs is present. Run: python scripts/check_install.py"""
import importlib
import shutil
import sys
from pathlib import Path

ok = True
for mod in ("pydantic", "httpx", "chromadb", "pcbcreator"):
    try:
        importlib.import_module(mod)
        print(f"[ok]   python module {mod}")
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"[FAIL] python module {mod}: {exc}")
for tool, why in (("kicad-cli", "ERC, schematic rendering"), ("pdftotext", "datasheet text"),
                  ("pdftoppm", "schematic PNG")):
    found = shutil.which(tool)
    ok &= bool(found)
    print(f"[{'ok' if found else 'FAIL'}]   {tool} ({why})")
sym = Path("/usr/share/kicad/symbols")
print(f"[{'ok' if sym.exists() else 'FAIL'}]   KiCad symbol libraries at {sym}")
ok &= sym.exists()
print("\nLocal model server (optional for tests): scripts/serve.sh, see README")
sys.exit(0 if ok else 1)
