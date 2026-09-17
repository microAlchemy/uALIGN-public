"""Repeatability and seed sensitivity.

Two different questions that are easy to conflate:

*Determinism* -- the same case at the same seed must produce the same layout.
A digest that moves between runs is a bug (uninitialized memory, map iteration
order, a timing-dependent solver cutoff) and blocks any use of golden files.
This is a check on every case, not a separate test: extra same-seed runs are
compared against the primary result and the findings land on that result.

*Seed sensitivity* -- how much the result moves across seeds. That is a
characterization number describing how much of the reported quality is luck,
and it belongs in the report rather than in a pass/fail gate. It is opt-in
(``--seeds``) because it multiplies runtime without being a correctness gate.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from ..case import Case
from ..result import Category, Finding, RunResult, Severity, Status

# Primary run plus one confirmation. ``--repeats 1`` skips the extra run;
# ``--repeats 3`` is the more expensive three-way comparison.
DEFAULT_REPEATS = 2

_SKIP_STATUSES = (Status.TIMEOUT, Status.CRASH, Status.HARNESS_ERROR)


@dataclass
class RepeatReport:
    results: list[RunResult]
    findings: list[Finding]

    @property
    def deterministic(self) -> bool:
        return not any(f.code in ("nondeterministic", "unstable_status") for f in self.findings)


def check(runner, case: Case, primary: RunResult, repeats: int = DEFAULT_REPEATS) -> list[Finding]:
    """Re-run at the same seed and compare layout digests against ``primary``.

    ``repeats`` counts the primary plus extra runs. 1 means do not check.
    Extra runs are skipped when the primary timed out or crashed, so a hung
    case is not replayed.
    """
    if repeats < 1:
        raise ValueError("repeats must be at least 1")
    if repeats == 1:
        return []
    if primary.status in _SKIP_STATUSES:
        return [Finding(
            category=Category.DETERMINISM, code="determinism_not_run", severity=Severity.INFO,
            message=f"skipped extra runs because the primary run {primary.status.value}",
        )]

    extras = [runner.run(case, tag=f"repeat{i}") for i in range(1, repeats)]
    return _compare_repeats([primary, *extras], seed=case.flow.seed)


def sweep_seeds(
    runner,
    case: Case,
    seeds: list[int],
    primary: RunResult | None = None,
) -> RepeatReport:
    """Run one case across several seeds to quantify how much quality is luck."""
    if len(seeds) < 2:
        raise ValueError("need at least two seeds")

    results: list[RunResult] = []
    for seed in seeds:
        if primary is not None and seed == primary.seed:
            results.append(primary)
        else:
            results.append(runner.run(case.with_flow(seed=seed), tag=f"seed{seed}"))

    return RepeatReport(results=results, findings=_seed_findings(results, seeds))


def _compare_repeats(results: list[RunResult], seed: int) -> list[Finding]:
    findings: list[Finding] = []
    digests = {r.layout_hash for r in results if r.layout_hash is not None}
    statuses = {r.status for r in results}

    if len(statuses) > 1:
        findings.append(Finding(
            category=Category.DETERMINISM, code="unstable_status", severity=Severity.ERROR,
            message="the same case at the same seed did not reach the same stage every run",
            data={"statuses": sorted(s.value for s in statuses)},
        ))

    if len(digests) > 1:
        findings.append(Finding(
            category=Category.DETERMINISM, code="nondeterministic", severity=Severity.ERROR,
            message=f"{len(digests)} distinct layouts from {len(results)} runs at seed {seed}",
            data={"digests": sorted(digests)},
        ))
    elif digests:
        findings.append(Finding(
            category=Category.DETERMINISM, code="deterministic", severity=Severity.INFO,
            message=f"identical layout across {len(results)} runs at seed {seed}",
            data={"digest": next(iter(digests))},
        ))

    runtimes = [r.wall_time_s for r in results]
    findings.append(Finding(
        category=Category.DETERMINISM, code="runtime_spread", severity=Severity.INFO,
        message=f"runtime {min(runtimes):.1f}-{max(runtimes):.1f}s over {len(results)} runs",
        data={"runtimes_s": [round(t, 2) for t in runtimes]},
    ))
    return findings


def _seed_findings(results: list[RunResult], seeds: list[int]) -> list[Finding]:
    findings: list[Finding] = []
    generated = [r for r in results if r.status is Status.OK]
    findings.append(Finding(
        category=Category.DETERMINISM, code="seed_success_rate", severity=Severity.INFO,
        message=f"{len(generated)}/{len(seeds)} seeds produced a layout",
        data={"seeds": seeds, "statuses": [r.status.value for r in results]},
    ))

    areas = [r.area_um2 for r in generated if r.area_um2 is not None]
    if len(areas) >= 2:
        best, worst = min(areas), max(areas)
        findings.append(Finding(
            category=Category.DETERMINISM, code="seed_area_spread", severity=Severity.INFO,
            message=f"area {best:.4f}-{worst:.4f} um2 across seeds (spread {worst / best - 1:.1%})",
            data={
                "best_um2": best,
                "worst_um2": worst,
                "median_um2": statistics.median(areas),
                "spread": worst / best - 1,
            },
        ))
    return findings
