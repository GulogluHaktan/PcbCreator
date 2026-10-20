"""Print a finished evaluation run as readable text (parts, nets) so it can be judged quickly.
Usage: python3 scripts/show_run.py evals/dev_r1/d0_led/run1"""
import json
import sys
from pathlib import Path

d = Path(sys.argv[1])
m = json.loads((d / "merged.module.json").read_text())
print(f"{m['name']}: {len(m['parts'])} parts")
for p in m["parts"]:
    print(f"  {p['ref']:<4} {p['part']:<44} {p.get('value') or ''}")
for n in m["nets"]:
    v = f" {n['voltage']} V" if n.get("voltage") is not None else ""
    print(f"  net {n['name']}{v}: {', '.join(n['pins'])}")
