"""Evaluation harness: run a list of requests several times each and record what happened.

A "gate pass" (status ok) is NOT the same as a correct board: two real designs passed every automatic gate and were
still wrong. So each run also gets a `human_verdict` slot (null until someone looks at the drawing) and the summary
reports both numbers. Results are written incrementally so a long run can be interrupted and inspected.
"""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from pathlib import Path
from typing import Callable

from .library import Library
from .pipeline import Pipeline, PipelineResult


def summarise_log(log: list[str]) -> dict:
    rules = Counter(m.group(1) for line in log if (m := re.search(r"(?:ERROR|WARNING)\s+([EWR]\d{3})", line)))
    rejected = sum(1 for line in log if re.search(r"block \d+/\d+ .*attempt \d+ rejected", line))
    module_fail = sum(1 for line in log if re.search(r"module \S+: attempt \d+ failed", line))
    handed = any("handing over to the module-level repair loop" in line for line in log)
    return {"block_rejections": rejected, "module_attempts_failed": module_fail, "handed_over": handed,
            "rules": dict(rules)}


def run_one(request: dict, run_idx: int, out_dir: Path, make_pipeline: Callable[[Path], Pipeline], lib: Library,
            draw: bool = True) -> dict:
    run_dir = out_dir / request["id"] / f"run{run_idx}"
    run_dir.mkdir(parents=True, exist_ok=True)
    pipe = make_pipeline(run_dir / "trace")
    t0 = time.time()
    try:
        res: PipelineResult = pipe.run(request["prompt"], name=request["id"])
        error = None
    except Exception as exc:  # noqa: BLE001  (an LLM server hiccup must not kill a long evaluation)
        res, error = PipelineResult(), f"{type(exc).__name__}: {exc}"[:300]
    rec = {"id": request["id"], "run": run_idx, "status": res.status if not error else "error", "error": error,
           "seconds": round(time.time() - t0), "modules": len(res.modules), "human_verdict": None,
           "requirement_kinds": [f"{r.count}x{r.kind}" for r in (res.requirements.required if res.requirements else [])],
           **summarise_log(res.log)}
    (run_dir / "log.txt").write_text("\n".join(res.log))
    if res.merge and res.merge.module is not None:
        (run_dir / "merged.module.json").write_text(res.merge.module.model_dump_json(indent=2))
        if draw:
            try:
                from .schematic import render

                r = render(res.merge.module, lib, run_dir / "schematic", request["id"])
                rec["drawing"] = {"style": r["style"], "connectivity_ok": not r["connectivity_diff"],
                                  "png": r.get("png")}
            except Exception as exc:  # noqa: BLE001
                rec["drawing"] = {"error": f"{type(exc).__name__}: {exc}"[:200]}
    (run_dir / "result.json").write_text(json.dumps(rec, indent=2))
    return rec


def write_summary(records: list[dict], out_dir: Path, meta: dict) -> None:
    (out_dir / "results.json").write_text(json.dumps({"meta": meta, "runs": records}, indent=2))
    lines = [f"# Evaluation summary ({meta.get('name', '')})", "",
             f"code: `{meta.get('commit', '?')}` · model: {meta.get('model', '?')} · runs/request: {meta.get('runs', '?')}",
             "", "| request | runs | gate pass | human-verified correct | median s | block rejections (mean) | "
             "most frequent rules |", "|---|---|---|---|---|---|---|"]
    for rid in dict.fromkeys(r["id"] for r in records):
        rs = [r for r in records if r["id"] == rid]
        ok = sum(r["status"] == "ok" for r in rs)
        judged = [r for r in rs if r["human_verdict"] is not None]
        good = sum(bool(r["human_verdict"]) for r in judged)
        secs = sorted(r["seconds"] for r in rs)
        rules = Counter()
        for r in rs:
            rules.update(r["rules"])
        top = ", ".join(f"{k}x{v}" for k, v in rules.most_common(4)) or "-"
        rej = sum(r["block_rejections"] for r in rs) / len(rs)
        lines.append(f"| {rid} | {len(rs)} | {ok}/{len(rs)} | "
                     f"{good}/{len(judged)} judged | {secs[len(secs) // 2]} | {rej:.1f} | {top} |")
    lines += ["", "*gate pass = every automatic gate (linter, coverage, reviewer, merge). It is not correctness: "
              "judge each drawing and set `human_verdict` (true/false) in its result.json, then re-run the summary.*"]
    (out_dir / "summary.md").write_text("\n".join(lines) + "\n")


def run_eval(requests: list[dict], runs: int, out_dir: str | Path, make_pipeline: Callable[[Path], Pipeline],
             lib: Library, meta: dict, draw: bool = True, echo: Callable[[str], None] = print) -> list[dict]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    records: list[dict] = []
    meta = {**meta, "runs": runs}
    for run_idx in range(1, runs + 1):  # round-robin over requests so a partial run still covers every request
        for request in requests:
            rec = run_one(request, run_idx, out, make_pipeline, lib, draw)
            records.append(rec)
            write_summary(records, out, meta)
            echo(f"[{request['id']} run{run_idx}] {rec['status']} in {rec['seconds']}s "
                 f"(block rejections {rec['block_rejections']})")
    return records
