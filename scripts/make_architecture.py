"""Draw assets/architecture.png (pipeline block diagram). Run: python3 scripts/make_architecture.py"""
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

LLM, CODE, STORE = "#ffe3b3", "#cfe8ff", "#d9f2d0"
boxes = [  # (x, y, w, h, text, colour)
    (0.2, 4.2, 2.3, 1.0, "User request", "white"),
    (3.0, 4.2, 2.6, 1.0, "Requirements\n+ required-parts checklist", LLM),
    (6.1, 4.2, 2.6, 1.0, "Architect\n(modules + port contracts)", LLM),
    (9.2, 4.2, 2.6, 1.0, "Contract check\n(code)", CODE),
    (0.2, 2.5, 2.9, 1.2, "Part scout (LLM queries\n+ code queries) -> shortlist\nof ~30-120 of 22,740 KiCad parts", LLM),
    (3.6, 2.5, 2.4, 1.2, "Selector (LLM)\nfinalists + datasheet\npart cards", LLM),
    (6.5, 2.5, 2.9, 1.2, "Block builder (LLM patch)\n<-> local lint: scope, pins,\nLED, connector ground", LLM),
    (0.2, 0.8, 2.9, 1.0, "Full lint + requirement\ncoverage (code)", CODE),
    (3.6, 0.8, 2.4, 1.0, "Reviewer (LLM)\nreasoning mandatory", LLM),
    (6.5, 0.8, 2.9, 1.0, "Merge modules (code)\n+ KiCad netlist", CODE),
    (9.9, 0.8, 1.9, 1.0, "KiCad schematic\n+ KiCad ERC", CODE),
    (2.4, -0.9, 3.6, 0.95, "Patch repair (LLM edit:\nremove / detach / add)", LLM),
]
fig, ax = plt.subplots(figsize=(13, 6.2))
for x, y, w, h, t, c in boxes:
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.04", fc=c, ec="#444", lw=1.2))
    ax.text(x + w / 2, y + h / 2, t, ha="center", va="center", fontsize=8.5)
arrows = [(2.5, 4.7, 3.0, 4.7), (5.6, 4.7, 6.1, 4.7), (8.7, 4.7, 9.2, 4.7), (10.5, 4.2, 1.6, 3.7),
          (3.1, 3.1, 3.6, 3.1), (6.0, 3.1, 6.5, 3.1), (7.9, 2.5, 1.6, 1.8),
          (3.1, 1.3, 3.6, 1.3), (6.0, 1.3, 6.5, 1.3), (9.4, 1.3, 9.9, 1.3)]
for x1, y1, x2, y2 in arrows:
    ax.annotate("", xy=(x2, y2), xytext=(x1, y1), arrowprops=dict(arrowstyle="->", color="#333", lw=1.2))
for x1, y1, x2, y2, t in [(4.8, 0.8, 4.8, 0.08, "findings"), (2.4, -0.42, 1.65, 0.8, "re-check")]:
    ax.annotate("", xy=(x2, y2), xytext=(x1, y1), arrowprops=dict(arrowstyle="->", color="#b34", lw=1.3, ls="--"))
    ax.text((x1 + x2) / 2 + 0.1, (y1 + y2) / 2, t, fontsize=7.5, color="#b34")
ax.add_patch(FancyBboxPatch((0.2, -2.1), 11.6, 0.55, boxstyle="round,pad=0.03", fc=STORE, ec="#444", lw=1.2))
ax.text(6.0, -1.82, "Chroma memory: parts index, datasheets, block patterns. LLM results are stored as 'llm_pending' "
        "and never reused until a human approves them", ha="center", va="center", fontsize=8.5)
ax.text(0.2, 5.55, "orange = LLM role     blue = deterministic code     green = memory", fontsize=9, va="bottom")
ax.set_xlim(0, 12); ax.set_ylim(-2.3, 5.9); ax.axis("off")
fig.savefig("assets/architecture.png", dpi=150, bbox_inches="tight")
