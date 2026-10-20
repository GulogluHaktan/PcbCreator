"""End-to-end orchestration: requirements -> architecture -> modules -> merge -> memory.

Every LLM output passes a deterministic gate before it moves on. Modules are only
written to memory when the *whole* design merges clean, so memory holds verified work.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import agents
import re

from .agents import Architecture, RequiredItem, Requirements
from .library import Library
from .coverage import check_coverage, request_checklist_findings
from .describe import describe_module
from .lint import Finding, Report, lint
from .llm import LLM
from .memory import DesignMemory
from .merge import MergeResult, check_contract, merge
from .stepwise import (apply_patch, apply_repair, blocks_from_requirements, initial_state, local_findings, scope_findings,
                       state_summary)
from .models import Design, Module


@dataclass
class PipelineResult:
    status: str = "failed"  # "ok" | "failed"
    requirements: Requirements | None = None
    architecture: Architecture | None = None
    modules: list[Module] = field(default_factory=list)
    module_reports: dict[str, Report] = field(default_factory=dict)
    merge: MergeResult | None = None
    log: list[str] = field(default_factory=list)


def architecture_findings(arch: Architecture, req: Requirements | None = None) -> list[Finding]:
    """Cheap structural check of the plan before any module is designed."""
    out: list[Finding] = []
    if req is not None and len(arch.modules) > 1:  # every checklist item must be given to some module
        for item in req.required:
            if item.kind in ("other", "support"):
                continue
            have = sum(r.count for m in arch.modules for r in m.required if r.kind == item.kind)
            if have < item.count:
                out.append(Finding("E100", "error", f"checklist item '{item.kind} x{item.count}: {item.detail}' is "
                                   f"assigned to modules {have} time(s)", "architecture",
                                   "list it under 'required' of the module that owns it"))
    ports = {(m.name, p.net) for m in arch.modules for p in m.ports}
    names = [m.name for m in arch.modules]
    if len(set(names)) != len(names):
        out.append(Finding("E100", "error", "duplicate module names in architecture", "architecture"))
    for m in arch.modules:  # the contract itself must obey the port rules, or no module can satisfy it
        for p in m.ports:
            loc = f"{m.name}.{p.net}"
            if p.kind == "power" and (p.direction not in ("power_in", "power_out") or p.voltage is None):
                out.append(Finding("E073", "error", f"power port {loc} must use direction power_in/power_out "
                                   "and have a voltage", loc))
            elif p.kind == "ground" and p.direction != "bidir":
                out.append(Finding("E073", "error", f"ground port {loc} must be bidir", loc))
            elif p.kind == "signal" and p.direction in ("power_in", "power_out"):
                out.append(Finding("E073", "error", f"signal port {loc} cannot be {p.direction}", loc))
    for text in [*(x for l in arch.links for x in (l.a, l.b)), *arch.expose]:
        mod, _, net = text.partition(".")
        if (mod, net) not in ports:
            out.append(Finding("E100", "error", f"{text} is not a port of any planned module", text))
    return out


def normalise_architecture(arch: Architecture, req: Requirements | None = None) -> Architecture:
    """A one-module board has nothing to link (every non-ground port leaves the board) and owns the whole checklist."""
    if len(arch.modules) == 1:
        m = arch.modules[0]
        if req is not None:
            m = m.model_copy(update={"required": list(req.required)})
        return arch.model_copy(update={"modules": [m], "links": [],
                                       "expose": [f"{m.name}.{p.net}" for p in m.ports if p.kind != "ground"]})
    return arch


class Pipeline:
    def __init__(self, llm: LLM, lib: Library, memory: DesignMemory | None = None, *,
                 strict: bool = False, max_module_repairs: int = 3, max_arch_repairs: int = 2,
                 echo: bool = False, cards=None, scout: bool = False, review: bool = False, stepwise: bool = True,
                 max_block_attempts: int = 6, max_restarts: int = 2, exclude_example_origins=None, trace_dir=None):
        self.echo, self.cards, self.trace_dir = echo, cards, trace_dir
        self.scout, self.review = scout, review
        self.stepwise, self.max_block_attempts = stepwise, max_block_attempts
        # LLM output is never trusted as an example until a human approves it (pcbc approve)
        self.exclude_example_origins = exclude_example_origins if exclude_example_origins is not None else ["llm_pending"]
        self.accepted_blocks: list[tuple] = []
        self.max_restarts = max_restarts
        self._base_t = getattr(llm, "temperature", 0.3)
        self.card_map: dict = {}
        self.llm, self.lib, self.memory, self.strict = llm, lib, memory, strict
        self.max_module_repairs, self.max_arch_repairs = max_module_repairs, max_arch_repairs

    def _trace(self, name: str, text: str) -> None:
        if self.trace_dir is not None:
            from pathlib import Path
            d = Path(self.trace_dir)
            d.mkdir(parents=True, exist_ok=True)
            (d / name).write_text(text)

    def _build_stepwise(self, plan, req, parts, cards_text, say):
        """Build the module block by block. Returns the assembled module (even if a block never got clean:
        the module-level repair loop then takes over), or None when there are no blocks."""
        blocks = blocks_from_requirements(req)
        if not blocks:
            return None
        state = initial_state(plan, plan.name)
        owned = {b.kind for b in blocks}
        for k, block in enumerate(blocks, start=1):
            patch, feedback = None, None
            examples = agents.block_examples_context(self.memory, block, 2, self.exclude_example_origins)
            self._trace(f"3_{plan.name}_block{k}_examples.txt", examples)
            for attempt in range(1, self.max_block_attempts + 1):
                patch = agents.design_block(self.llm, plan, req, block, state_summary(state, self.lib), parts,
                                            self.lib, cards_text, patch, feedback, examples)
                new_state, errs = apply_patch(state, patch)
                errs = errs or scope_findings(patch, block, self.lib, owned) or local_findings(new_state, patch, self.lib,
                                                                                    self.card_map)
                self._trace(f"3_{plan.name}_block{k}_attempt{attempt}.txt",
                            f"BLOCK: {block.instruction}\n\nREASONING\n{patch.reasoning}\n\n"
                            + describe_module(new_state, self.lib) + "\n\nLOCAL LINT\n"
                            + ("\n".join(map(str, errs)) or "clean"))
                if not errs:
                    state = new_state
                    self.accepted_blocks.append((block, patch))
                    say(f"block {k}/{len(blocks)} [{block.title}]: ok (attempt {attempt})")
                    break
                feedback = errs
                say(f"block {k}/{len(blocks)} [{block.title}]: attempt {attempt} rejected with {len(errs)} issues")
                for f in errs:
                    say(f"    {f}")
            else:
                # a dirty block would poison every later block: stop here and let the module-level repair loop
                # (which sees the whole module and the full lint) take over from this state
                say(f"block {k}/{len(blocks)} [{block.title}]: not clean after {self.max_block_attempts} attempts; "
                    "handing over to the module-level repair loop")
                return new_state
        return state

    def _build_and_repair(self, plan, module_req, parts, cards_text, say, suffix: str = ""):
        """One full attempt at a module: stepwise build, then lint / coverage / reviewer / patch repair rounds."""
        module = report = None
        feedback: list[Finding] | None = None
        built, last_patch = None, None
        if self.stepwise and module_req.required:
            built = self._build_stepwise(plan, module_req, parts, cards_text, say)
        for attempt in range(self.max_module_repairs + 1):
            if attempt == 0 and built is not None:
                module = built
            elif built is not None and feedback:
                # patch-based repair: rewriting the whole module let the model break what already worked
                patch = agents.design_repair(self.llm, plan, module_req, state_summary(module, self.lib),
                                             parts, self.lib, cards_text, feedback, last_patch)
                module, rerrs = apply_repair(module, patch, self.lib)
                module = module.model_copy(update={"name": plan.name})
                last_patch = patch
                self._trace(f"3_{plan.name}{suffix}_repair{attempt}.json", patch.model_dump_json(indent=2))
                if rerrs:
                    say("    repair edit had problems: " + "; ".join(str(f) for f in rerrs))
            else:
                module = agents.design_module(self.llm, plan, module_req, self.lib, self.memory,
                                              parts, cards_text, module, feedback)
            module = module.model_copy(update={"name": plan.name})
            report = lint(module, self.lib, strict=self.strict, cards=self.card_map)
            report.findings.extend(check_contract(module, plan.ports))
            report.findings.extend(check_coverage(module, self.lib, module_req.required))
            if report.passed and self.review:
                review = agents.review_module(self.llm, module_req, module, self.lib, cards_text)
                self._trace(f"3_{plan.name}{suffix}_attempt{attempt + 1}_review.json", review.model_dump_json(indent=2))
                report.findings.extend(agents.review_findings(review, plan.name))
            self._trace(f"3_{plan.name}{suffix}_attempt{attempt + 1}.txt",
                        describe_module(module, self.lib) + "\n\nREASONING\n" + module.reasoning
                        + "\n\nLINT\n" + report.to_text())
            self._trace(f"3_{plan.name}{suffix}_attempt{attempt + 1}.json", module.model_dump_json(indent=2))
            if report.passed:
                break
            feedback = report.errors
            say(f"module {plan.name}: attempt {attempt + 1} failed with {len(report.errors)} errors")
            for f in report.errors:
                say(f"    {f}")
        return module, report

    def _set_temperature(self, t: float) -> None:
        if hasattr(self.llm, "temperature"):
            self.llm.temperature = t

    def run(self, request: str, name: str = "design") -> PipelineResult:
        res = PipelineResult()
        def say(line: str) -> None:
            res.log.append(line)
            if self.echo:
                print(line, flush=True)

        res.requirements = agents.elicit_requirements(self.llm, request)
        for attempt in range(2):  # the checklist must cover every part the request names
            gaps = request_checklist_findings(request, res.requirements)
            if not gaps:
                break
            say(f"requirements incomplete (attempt {attempt + 1}): " + "; ".join(f.message for f in gaps))
            res.requirements = agents.elicit_requirements(self.llm, request, gaps, res.requirements)
        else:
            gaps = request_checklist_findings(request, res.requirements)
            if gaps:  # last resort, deterministic: add what the request plainly names
                kinds = {"led": "led", "regulator": "linear_regulator", "sensor": "sensor", "MCU": "mcu"}
                extra = []
                for f in gaps:
                    for word, kind in kinds.items():
                        if f"'{kind}'" in f.message:
                            extra.append(RequiredItem(kind=kind, detail=f"named in the request ({word})"))
                    if "connector" in f.message:
                        have = sum(r.count for r in res.requirements.required if r.kind == "connector")
                        need = int(re.search(r"names (\d+)", f.message).group(1))
                        extra.append(RequiredItem(kind="connector", count=need - have, detail="named in the request"))
                if extra:
                    say("requirements still incomplete; added from the request text: "
                        + ", ".join(f"{e.count}x{e.kind}" for e in extra))
                    res.requirements = res.requirements.model_copy(
                        update={"required": res.requirements.required + extra})
        self._trace("1_requirements.json", res.requirements.model_dump_json(indent=2))
        say("requirements: ok")

        arch = normalise_architecture(agents.plan_architecture(self.llm, res.requirements, self.lib, self.memory),
                                      res.requirements)
        for attempt in range(self.max_arch_repairs):
            problems = architecture_findings(arch, res.requirements)
            if not problems:
                break
            say(f"architecture invalid (attempt {attempt + 1}): {len(problems)} problems")
            say("    plan: " + arch.model_dump_json()[:700])
            for f in problems:
                say(f"    {f}")
            arch = normalise_architecture(
                agents.plan_architecture(self.llm, res.requirements, self.lib, self.memory, problems, arch),
                res.requirements)
        else:
            if architecture_findings(arch, res.requirements):
                say("architecture still invalid; giving up")
                res.architecture = arch
                return res
        res.architecture = arch
        self._trace("2_architecture.json", arch.model_dump_json(indent=2))
        say(f"architecture: {[m.name for m in arch.modules]}")

        single = len(arch.modules) == 1
        for plan in arch.modules:
            module, report = None, None
            feedback: list[Finding] | None = None
            # the checklist this module is responsible for (all of it for a one-module board)
            module_req = res.requirements.model_copy(update={"required": plan.required or (
                res.requirements.required if single else [])})
            queries = [plan.purpose]
            if self.scout:
                scout = agents.scout_parts(self.llm, plan, module_req)
                self._trace(f"3_{plan.name}_scout.json", scout.model_dump_json(indent=2))
                model_q = [q.query for q in scout.queries if len(q.query.split()) >= 2]  # bare labels are noise
                queries = list(dict.fromkeys(agents.code_queries(module_req) + model_q)) or queries
                say(f"module {plan.name}: part searches: {queries}")
                if scout.unavailable:
                    say(f"module {plan.name}: needs a human, no library part for: {scout.unavailable}")
                    res.status = "needs_human"
                    return res
            if self.scout and self.lib.large and self.memory is not None:
                short = agents.shortlist(self.lib, self.memory, queries)
                self._trace(f"3_{plan.name}_shortlist.txt", "\n".join(f"{p.id} | {p.description[:100]}" for p in short))
                if short:
                    selection = agents.select_parts(self.llm, plan, module_req, short)
                    self._trace(f"3_{plan.name}_selection.json", selection.model_dump_json(indent=2))
                    parts = agents.finalist_parts(self.lib, selection, short)
                    say(f"module {plan.name}: shortlist {len(short)} ICs -> finalists "
                        f"{[f.id for f in selection.finalists if f.id in {p.id for p in parts}]}")
                else:
                    parts = agents.candidate_parts(self.lib, None, [])
                    say(f"module {plan.name}: no special ICs needed; generic parts only")
            else:
                parts = agents.candidate_parts(self.lib, self.memory, queries)
            cards_text = agents.cards_context(parts, self.cards)
            self.card_map = ({p.id: c for p in parts if p.category == "ic" and (c := self.cards.peek(p)) is not None}
                             if self.cards is not None else {})
            self._trace(f"3_{plan.name}_candidates.txt",
                        "\n".join(self.lib.summary(p) for p in parts) + "\n\nCARDS\n" + cards_text)
            module = report = None
            kept_blocks = len(self.accepted_blocks)
            for restart in range(self.max_restarts + 1):
                if restart:  # pass@k style: a fresh sample, a little hotter, from scratch
                    del self.accepted_blocks[kept_blocks:]  # blocks of the failed attempt must not reach memory
                    say(f"module {plan.name}: restart {restart}/{self.max_restarts} with a fresh sample")
                    self._set_temperature(min(1.0, self._base_t + 0.2 * restart))
                module, report = self._build_and_repair(plan, module_req, parts, cards_text, say,
                                                        f"_r{restart}" if restart else "")
                if report.passed:
                    break
            self._set_temperature(self._base_t)
            assert module is not None and report is not None
            res.modules.append(module)
            res.module_reports[plan.name] = report
            if not report.passed:
                say(f"module {plan.name}: FAILED after repairs")
                return res
            say(f"module {plan.name}: ok ({len(report.warnings)} warnings)")

        design = Design(name=name, modules=res.modules, links=arch.links, expose=arch.expose)
        res.merge = merge(design, self.lib, strict=self.strict)
        for attempt in range(self.max_arch_repairs):
            if res.merge.ok:
                break
            errs = [f for f in res.merge.findings if f.severity == "error"]
            say(f"merge failed: {len(errs)} errors; asking architect to repair links (attempt {attempt + 1})")
            for f in errs:
                say(f"    {f}")
            fixed = agents.plan_architecture(self.llm, res.requirements, self.lib, self.memory, errs, arch)
            if [m.model_dump() for m in fixed.modules] != [m.model_dump() for m in arch.modules]:
                say("architect changed module contracts; modules would need redesign (not supported yet)")
                break
            arch = fixed
            design = Design(name=name, modules=res.modules, links=arch.links, expose=arch.expose)
            res.merge = merge(design, self.lib, strict=self.strict)
        res.architecture = arch
        if not res.merge.ok:
            say("merge: FAILED")
            return res

        if not single:
            cov = check_coverage(res.merge.module, self.lib, res.requirements.required)
            res.merge.findings.extend(cov)
            if cov:
                say("merge ok but requirement coverage failed:")
                for f in cov:
                    say(f"    {f}")
                return res
        say("merge: ok")
        if self.memory is not None:
            for m in res.modules:
                self.memory.add_module(m, res.module_reports[m.name], origin="llm_pending")
            for block, patch in self.accepted_blocks:
                self.memory.add_block(block.kind, block.instruction, patch.model_dump_json(),
                                      [p.part for p in patch.parts], origin="llm_pending", source=name)
            say(f"stored {len(res.modules)} verified modules and {len(self.accepted_blocks)} blocks in memory")
        res.status = "ok"
        return res
