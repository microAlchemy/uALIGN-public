"""Expectations, recorded as data.

Most cases are expected to fail at the start of a qualification exercise --
that is the finding, not a broken suite. So tests do not assert absolute
correctness; they assert that a case did no worse than its recorded
expectation, and complain just as loudly when a case does *better* than
expected, because that means someone fixed something and the record is now
stale. The result is a ratchet whose direction of travel is reportable:
"37 of 210 primitives currently broken, down from 52".

Expectations live in ``qual/baselines/known_issues.yaml``, keyed by case uid::

    diff_pair_nmos__Bulk45nm_Mock_PDK:
      outcome: fail
      status: primitive_fail
      reason: "W quantized to the fin grid on a planar PDK"
      issue: MA-112

A case with no entry is expected to pass cleanly. Snapshot the current state
with ``pytest --update-baseline`` and review the diff.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

import yaml

from .case import Case
from .paths import BASELINES_DIR
from .result import LvsStatus, Outcome, RunResult, Status

DEFAULT_BASELINE = BASELINES_DIR / "known_issues.yaml"
DEFAULT_AREA_TOLERANCE = 0.15

_EXPECTATION_KEYS = {"outcome", "status", "drc_errors", "lvs", "area_um2", "reason", "issue"}


class Direction(str, Enum):
    REGRESSION = "regression"
    IMPROVEMENT = "improvement"


@dataclass(frozen=True)
class Violation:
    direction: Direction
    field: str
    expected: Any
    actual: Any

    def __str__(self) -> str:
        return f"{self.direction.value}: {self.field} expected {self.expected!r}, got {self.actual!r}"


@dataclass
class Expectation:
    outcome: Outcome = Outcome.PASS
    status: Status | None = None
    drc_errors: int = 0
    lvs: LvsStatus | None = None
    area_um2: float | None = None
    reason: str = ""
    issue: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any], key: str) -> Expectation:
        unknown = set(d) - _EXPECTATION_KEYS
        if unknown:
            raise ValueError(f"{key}: unknown expectation keys {sorted(unknown)}")
        return cls(
            outcome=Outcome(d.get("outcome", "pass")),
            status=Status(d["status"]) if d.get("status") else None,
            drc_errors=int(d.get("drc_errors", 0)),
            lvs=LvsStatus(d["lvs"]) if d.get("lvs") else None,
            area_um2=d.get("area_um2"),
            reason=d.get("reason", ""),
            issue=d.get("issue", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"outcome": self.outcome.value}
        if self.status is not None:
            d["status"] = self.status.value
        if self.drc_errors:
            d["drc_errors"] = self.drc_errors
        if self.lvs is not None:
            d["lvs"] = self.lvs.value
        if self.area_um2 is not None:
            d["area_um2"] = round(self.area_um2, 6)
        if self.reason:
            d["reason"] = self.reason
        if self.issue:
            d["issue"] = self.issue
        return d

    @classmethod
    def from_result(cls, result: RunResult, previous: Expectation | None = None) -> Expectation:
        """Snapshot a result, preserving any human-written reason and issue."""
        return cls(
            outcome=result.outcome,
            status=None if result.status is Status.OK else result.status,
            drc_errors=result.drc_errors,
            lvs=None if result.lvs is LvsStatus.NOT_RUN else result.lvs,
            area_um2=result.area_um2,
            reason=previous.reason if previous else "",
            issue=previous.issue if previous else "",
        )


# Better-to-worse ordering, so a change can be called a regression or a fix.
_OUTCOME_RANK = {Outcome.PASS: 0, Outcome.PASS_WITH_DRC: 1, Outcome.FAIL: 2}


@dataclass
class Baseline:
    path: Path = DEFAULT_BASELINE
    area_tolerance: float = DEFAULT_AREA_TOLERANCE
    entries: dict[str, Expectation] = field(default_factory=dict)
    _dirty: bool = False

    @classmethod
    def load(cls, path: Path = DEFAULT_BASELINE, area_tolerance: float = DEFAULT_AREA_TOLERANCE) -> Baseline:
        entries: dict[str, Expectation] = {}
        if path.is_file():
            raw = yaml.safe_load(path.read_text()) or {}
            if not isinstance(raw, dict):
                raise ValueError(f"{path}: top level must be a mapping of case uid to expectation")
            entries = {key: Expectation.from_dict(value or {}, key) for key, value in raw.items()}
        return cls(path=path, area_tolerance=area_tolerance, entries=entries)

    def expectation_for(self, case: Case) -> Expectation:
        return self.entries.get(case.uid, Expectation())

    def compare(self, case: Case, result: RunResult) -> list[Violation]:
        expected = self.expectation_for(case)
        violations: list[Violation] = []

        actual_rank = _OUTCOME_RANK[result.outcome]
        expected_rank = _OUTCOME_RANK[expected.outcome]
        if actual_rank > expected_rank:
            violations.append(Violation(Direction.REGRESSION, "outcome",
                                        expected.outcome.value, result.outcome.value))
        elif actual_rank < expected_rank:
            violations.append(Violation(Direction.IMPROVEMENT, "outcome",
                                        expected.outcome.value, result.outcome.value))

        if result.drc_errors > expected.drc_errors:
            violations.append(Violation(Direction.REGRESSION, "drc_errors",
                                        expected.drc_errors, result.drc_errors))
        elif result.drc_errors < expected.drc_errors:
            violations.append(Violation(Direction.IMPROVEMENT, "drc_errors",
                                        expected.drc_errors, result.drc_errors))

        # Area is a guardrail, not an equality check: the placer is stochastic,
        # so only a move outside the tolerance band is worth failing on.
        if expected.area_um2 and result.area_um2:
            ratio = result.area_um2 / expected.area_um2
            if ratio > 1 + self.area_tolerance:
                violations.append(Violation(Direction.REGRESSION, "area_um2",
                                            f"<= {expected.area_um2 * (1 + self.area_tolerance):.4f}",
                                            round(result.area_um2, 4)))

        return violations

    def explain(self, case: Case, result: RunResult, violations: list[Violation]) -> str:
        expected = self.expectation_for(case)
        known = case.uid in self.entries
        lines = [result.summary_line()]
        for violation in violations:
            lines.append(f"  {violation}")
        if expected.reason:
            lines.append(f"  recorded reason: {expected.reason}")
        if expected.issue:
            lines.append(f"  tracked as: {expected.issue}")
        if not known:
            lines.append(f"  no baseline entry for {case.uid}; a case with no entry must pass cleanly")
        if result.artifacts_dir:
            lines.append(f"  artifacts: {result.artifacts_dir}")
        if any(v.direction is Direction.IMPROVEMENT for v in violations):
            lines.append("  this is an improvement: refresh with `pytest --update-baseline`")
        return "\n".join(lines)

    def record(self, result: RunResult) -> None:
        """Snapshot one result. Keyed off the result so that the baseline can
        be rebuilt from results.jsonl by the controlling process, rather than
        from per-worker state that a parallel run would drop."""
        self.entries[result.case_uid] = Expectation.from_result(
            result, self.entries.get(result.case_uid)
        )
        self._dirty = True

    def record_all(self, results: Iterable[RunResult]) -> int:
        for result in results:
            self.record(result)
        return len(self.entries)

    def save(self) -> None:
        if not self._dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {key: self.entries[key].to_dict() for key in sorted(self.entries)}
        self.path.write_text(yaml.safe_dump(payload, sort_keys=False, default_flow_style=False))
        self._dirty = False
