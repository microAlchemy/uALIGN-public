"""Result schema.

One ``RunResult`` per case per run. This is the unit of record for the whole
suite: reports, baselines and trend comparisons all read it, and nothing else
is allowed to be the source of truth. Checks contribute ``Finding`` objects
rather than raising, so that a case with twelve DRC errors is a measurement
rather than a stack trace.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

# ALIGN layout JSON stores coordinates in units of 1e-4 um (see
# bin/analyze_regression.py, which divides bbox extents by 10000).
LAYOUT_UNITS_PER_UM = 10_000

# Error buckets kept deliberately identical to ALIGN's own regression summaries
# so historical summary-*.csv files remain comparable to ours.
DRC_BUCKETS = ("open", "short", "minlen", "terminal", "other")


class Status(str, Enum):
    """How far the flow got. Ordered from best to worst."""

    OK = "ok"
    ROUTE_FAIL = "route_fail"
    PLACE_FAIL = "place_fail"
    PRIMITIVE_FAIL = "primitive_fail"
    TOPOLOGY_FAIL = "topology_fail"
    TIMEOUT = "timeout"
    CRASH = "crash"
    HARNESS_ERROR = "harness_error"

    @property
    def generated(self) -> bool:
        return self is Status.OK


class LvsStatus(str, Enum):
    NOT_RUN = "not_run"
    CLEAN = "clean"
    MISMATCH = "mismatch"
    ERROR = "error"


class Outcome(str, Enum):
    """Coarse result used for baseline comparison."""

    PASS = "pass"
    PASS_WITH_DRC = "pass_with_drc"
    FAIL = "fail"


class Cleanup(str, Enum):
    """How much of the layout a human would still have to do.

    Derived from recorded issues, not a separate measurement. ``complete``
    means there is no first-pass layout to start from.
    """

    NONE = "none"
    MINOR = "minor"
    MAJOR = "major"
    COMPLETE = "complete"


# Phase 1 recording contract: every RunResult.metrics() must include keys that
# cover this list. The values are the metric names that satisfy each item.
RECORDING = (
    ("generation success/failure", ("generated", "status")),
    ("runtime", ("wall_time_s",)),
    ("DRC violations", ("align_drc_errors", "signoff_drc_errors")),
    ("LVS status", ("lvs",)),
    ("area", ("area", "w", "h")),
    ("major routing issues", ("routing_issues", "open", "short")),
    ("determinism/repeatability", ("deterministic",)),
    ("amount of manual cleanup required", ("cleanup", "cleanup_count")),
)


def recording_gaps(metrics: dict[str, Any]) -> list[str]:
    """Requirements whose metric keys are missing from a metrics dict."""
    return [name for name, keys in RECORDING if not any(k in metrics for k in keys)]


class Category(str, Enum):
    FLOW = "flow"
    DRC = "drc"
    LVS = "lvs"
    GEOMETRY = "geometry"
    ROUTING = "routing"
    DETERMINISM = "determinism"
    ANALOG = "analog"
    SIMULATION = "simulation"


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass
class Finding:
    """A single observation about a run.

    ``data`` carries the numbers that make a finding actionable -- for a
    geometry finding, the requested and realized dimensions, so a quantization
    bug is visible as a pattern across a sweep rather than a pass/fail flag.
    """

    category: Category
    code: str
    severity: Severity
    message: str
    location: str | None = None
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["category"] = self.category.value
        d["severity"] = self.severity.value
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Finding:
        return cls(
            category=Category(d["category"]),
            code=d["code"],
            severity=Severity(d["severity"]),
            message=d["message"],
            location=d.get("location"),
            data=d.get("data", {}),
        )


@dataclass
class RunResult:
    case_id: str
    # Identity including the PDK. Carried in the record so that results.jsonl
    # is self-sufficient: the baseline can be rebuilt from it without
    # re-loading the case files.
    case_uid: str
    kind: str
    pdk: str
    status: Status

    # Provenance. A result without these is not comparable to anything.
    align_revision: str = "unknown"
    align_dirty: bool = False
    harness_version: str = "unknown"
    seed: int = 0
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    cmdline: list[str] = field(default_factory=list)

    wall_time_s: float = 0.0
    peak_rss_mb: float | None = None

    # Geometry, in um and um^2.
    width_um: float | None = None
    height_um: float | None = None

    # Analog figures of merit (symmetry, efficiency, parasitics)
    active_area_um2: float | None = None
    area_efficiency_pct: float | None = None
    total_wirelength_um: float | None = None
    total_vias: int | None = None
    max_diff_net_mismatch_pct: float | None = None
    max_diff_via_delta: int | None = None
    analog_quality_score: float | None = None
    sim_metrics: dict[str, float] = field(default_factory=dict)

    # The flow's own DRC count and an external signoff count are tracked
    # separately on purpose: whether "0 ALIGN errors" implies "0 signoff
    # errors" is one of the questions this suite exists to answer.
    align_drc_errors: int | None = None
    signoff_drc_errors: int | None = None
    lvs: LvsStatus = LvsStatus.NOT_RUN

    # Canonicalized digest of the layout, for repeat-run comparison.
    layout_hash: str | None = None

    findings: list[Finding] = field(default_factory=list)
    artifacts_dir: str | None = None

    @property
    def area_um2(self) -> float | None:
        if self.width_um is None or self.height_um is None:
            return None
        return self.width_um * self.height_um

    @property
    def aspect(self) -> float | None:
        if not self.width_um or not self.height_um:
            return None
        return max(self.width_um / self.height_um, self.height_um / self.width_um)

    @property
    def drc_errors(self) -> int:
        """Signoff count when we have one, otherwise the flow's self-report."""
        if self.signoff_drc_errors is not None:
            return self.signoff_drc_errors
        return self.align_drc_errors or 0

    def bucket_counts(self) -> dict[str, int]:
        """Per-bucket violation counts, using the count stored on each finding."""
        counts = {bucket: 0 for bucket in DRC_BUCKETS}
        for f in self.findings:
            if f.category not in (Category.DRC, Category.ROUTING) or f.code not in DRC_BUCKETS:
                continue
            counts[f.code] += int(f.data.get("count", 1))
        return counts

    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.ERROR]

    @property
    def generated(self) -> bool:
        return self.status.generated

    @property
    def routing_issues(self) -> int:
        """Opens plus shorts: the routing problems that force real rework."""
        buckets = self.bucket_counts()
        return buckets["open"] + buckets["short"]

    @property
    def cleanup_count(self) -> int | None:
        """Actionable remaining issues. None when there is no layout at all."""
        if not self.generated:
            return None
        count = self.drc_errors
        count += sum(
            1 for f in self.findings
            if f.category is Category.GEOMETRY and f.severity is Severity.ERROR
        )
        if self.lvs in (LvsStatus.MISMATCH, LvsStatus.ERROR):
            count += 1
        return count

    @property
    def cleanup(self) -> Cleanup:
        if not self.generated:
            return Cleanup.COMPLETE
        if (
            self.routing_issues > 0
            or self.lvs in (LvsStatus.MISMATCH, LvsStatus.ERROR)
            or any(f.category is Category.GEOMETRY and f.severity is Severity.ERROR for f in self.findings)
        ):
            return Cleanup.MAJOR
        if self.drc_errors > 0:
            return Cleanup.MINOR
        return Cleanup.NONE

    @property
    def deterministic(self) -> bool | None:
        """None if not checked, otherwise whether same-seed runs agreed."""
        codes = {f.code for f in self.findings if f.category is Category.DETERMINISM}
        if "nondeterministic" in codes or "unstable_status" in codes:
            return False
        if "deterministic" in codes:
            return True
        return None

    @property
    def outcome(self) -> Outcome:
        if not self.status.generated:
            return Outcome.FAIL
        if self.lvs is LvsStatus.MISMATCH or self.lvs is LvsStatus.ERROR:
            return Outcome.FAIL
        if any(
            f.severity is Severity.ERROR
            and f.category in (Category.GEOMETRY, Category.DETERMINISM)
            for f in self.findings
        ):
            return Outcome.FAIL
        if self.drc_errors > 0:
            return Outcome.PASS_WITH_DRC
        return Outcome.PASS

    def metrics(self) -> dict[str, Any]:
        """Flat scalars, for the summary table and JUnit properties."""
        m: dict[str, Any] = {
            "case_id": self.case_id,
            "case_uid": self.case_uid,
            "kind": self.kind,
            "pdk": self.pdk,
            "status": self.status.value,
            "generated": self.generated,
            "outcome": self.outcome.value,
            "wall_time_s": round(self.wall_time_s, 3),
            "peak_rss_mb": None if self.peak_rss_mb is None else round(self.peak_rss_mb, 1),
            "align_drc_errors": self.align_drc_errors,
            "signoff_drc_errors": self.signoff_drc_errors,
            "lvs": self.lvs.value,
            "w": self.width_um,
            "h": self.height_um,
            "area": self.area_um2,
            "aspect": None if self.aspect is None else round(self.aspect, 4),
            "area_efficiency_pct": self.area_efficiency_pct,
            "total_wirelength_um": self.total_wirelength_um,
            "total_vias": self.total_vias,
            "max_diff_net_mismatch_pct": self.max_diff_net_mismatch_pct,
            "max_diff_via_delta": self.max_diff_via_delta,
            "analog_quality_score": self.analog_quality_score,
            "layout_hash": self.layout_hash,
            "routing_issues": self.routing_issues,
            "deterministic": self.deterministic,
            "cleanup": self.cleanup.value,
            "cleanup_count": self.cleanup_count,
            "align_revision": self.align_revision,
            "align_dirty": self.align_dirty,
            "seed": self.seed,
            "timestamp": self.timestamp,
        }
        m.update(self.bucket_counts())
        return m

    def to_dict(self) -> dict[str, Any]:
        return {
            **{k: v for k, v in asdict(self).items() if k not in ("status", "lvs", "findings")},
            "status": self.status.value,
            "lvs": self.lvs.value,
            "findings": [f.to_dict() for f in self.findings],
            "derived": {
                "outcome": self.outcome.value,
                "generated": self.generated,
                "area_um2": self.area_um2,
                "aspect": self.aspect,
                "routing_issues": self.routing_issues,
                "deterministic": self.deterministic,
                "cleanup": self.cleanup.value,
                "cleanup_count": self.cleanup_count,
                **self.bucket_counts(),
            },
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RunResult:
        d = {k: v for k, v in d.items() if k != "derived"}
        known = set(cls.__dataclass_fields__.keys())
        filtered = {k: v for k, v in d.items() if k in known}
        return cls(
            **{**filtered,
               "status": Status(d["status"]),
               "lvs": LvsStatus(d.get("lvs", "not_run")),
               "findings": [Finding.from_dict(f) for f in d.get("findings", [])]}
        )

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2) + "\n")

    def summary_line(self) -> str:
        parts = [f"{self.case_id} [{self.pdk}]", self.status.value, f"{self.wall_time_s:.1f}s"]
        if self.area_um2 is not None:
            parts.append(f"area={self.area_um2:.4f}um2")
        parts.append(f"drc={self.drc_errors}")
        if self.routing_issues:
            parts.append(f"routing={self.routing_issues}")
        if self.lvs is not LvsStatus.NOT_RUN:
            parts.append(f"lvs={self.lvs.value}")
        parts.append(f"cleanup={self.cleanup.value}")
        if self.deterministic is not None:
            parts.append("deterministic" if self.deterministic else "nondeterministic")
        return "  ".join(parts)
