"""ALIGN adapter: Case in, RunResult out.

This is the only module that knows how ALIGN is invoked and where it leaves its
output, so swapping in a replacement placer or router means writing a sibling
of this file and nothing else.

The flow runs as a subprocess rather than in-process (which is how ALIGN's own
tests do it) for three reasons: an abort inside the compiled PnR extension
cannot then take down the pytest session and lose every result collected so
far; a hung ILP solve becomes a recorded TIMEOUT instead of a hung CI job; and
ALIGN's own harness calls os.chdir, which is process-global and unsafe once
cases run in parallel.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import HARNESS_VERSION
from .case import Case
from .paths import DEFAULT_ARTIFACTS_ROOT, AlignInstall
from .result import (
    LAYOUT_UNITS_PER_UM,
    Category,
    Finding,
    RunResult,
    Severity,
    Status,
)

# ru_maxrss is bytes on Darwin and kilobytes on Linux.
_RSS_DIVISOR = 1024 * 1024 if sys.platform == "darwin" else 1024

# Error-line taxonomy copied from ALIGN's bin/analyze_regression.py so that our
# per-bucket counts line up with the historical regression_summaries/*.csv.
_ERROR_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("open", re.compile(r"^OPEN .*$")),
    ("short", re.compile(r"^SHORT .*$")),
    ("minlen", re.compile(r"^DRC ERROR MinLength violation .*$")),
    ("terminal", re.compile(r"^DRC ERROR Terminal .*$")),
)

_ROUTING_BUCKETS = {"open", "short"}


@dataclass
class Invocation:
    returncode: int
    timed_out: bool
    peak_rss_mb: float | None
    wall_time_s: float


class Runner:
    """Runs cases against a resolved ALIGN install."""

    def __init__(
        self,
        align: AlignInstall,
        artifacts_root: Path = DEFAULT_ARTIFACTS_ROOT,
        keep_run_dir: bool = True,
    ) -> None:
        self.align = align
        self.artifacts_root = Path(artifacts_root)
        self.keep_run_dir = keep_run_dir
        self._revision = align.revision()
        self._dirty = align.is_dirty()

    # -- public API ----------------------------------------------------------

    def case_dir(self, case: Case, tag: str | None = None) -> Path:
        name = case.uid if tag is None else f"{case.uid}__{tag}"
        return self.artifacts_root / case.kind / name

    def run(self, case: Case, tag: str | None = None) -> RunResult:
        """Run one case. ``tag`` separates artifacts for repeat runs."""
        base = self.case_dir(case, tag)
        if base.exists():
            shutil.rmtree(base)
        design_dir = self._materialize(case, base / "design")
        run_dir = base / "run"
        run_dir.mkdir(parents=True)

        argv = self._argv(case, design_dir, run_dir)
        (base / "cmdline.txt").write_text(" ".join(argv) + "\n")

        result = RunResult(
            case_id=case.id,
            case_uid=case.uid,
            kind=case.kind,
            pdk=case.pdk,
            status=Status.HARNESS_ERROR,
            align_revision=self._revision,
            align_dirty=self._dirty,
            harness_version=HARNESS_VERSION,
            seed=case.flow.seed,
            cmdline=argv,
            artifacts_dir=str(base),
        )

        invocation = _spawn(
            argv, cwd=run_dir, log_path=base / "align.log",
            timeout_s=case.timeout_s, env=self.align.process_env(),
        )
        result.wall_time_s = invocation.wall_time_s
        result.peak_rss_mb = invocation.peak_rss_mb
        result.status = self._classify(case, run_dir, invocation)

        if invocation.timed_out:
            result.findings.append(Finding(
                category=Category.FLOW, code="timeout", severity=Severity.ERROR,
                message=f"killed after {case.timeout_s}s",
            ))
        elif invocation.returncode < 0:
            result.findings.append(Finding(
                category=Category.FLOW, code="signal", severity=Severity.ERROR,
                message=f"terminated by signal {-invocation.returncode}"
                        f" ({signal.Signals(-invocation.returncode).name})",
            ))
        elif invocation.returncode != 0:
            result.findings.append(Finding(
                category=Category.FLOW, code="nonzero_exit", severity=Severity.ERROR,
                message=f"exit status {invocation.returncode}; see align.log",
                data={"tail": _log_tail(base / "align.log")},
            ))

        self._collect_geometry(case, run_dir, result)
        self._collect_flow_errors(case, run_dir, result)
        result.layout_hash = self._layout_hash(case, run_dir)

        result.write(base / "result.json")
        if not self.keep_run_dir:
            shutil.rmtree(run_dir, ignore_errors=True)
        return result

    # -- setup ---------------------------------------------------------------

    def _materialize(self, case: Case, design_dir: Path) -> Path:
        """Write the case's netlist and constraints into a design directory."""
        design_dir.mkdir(parents=True)
        (design_dir / f"{case.subckt}.sp").write_text(case.netlist)
        for subckt, constraints in case.constraints.items():
            path = design_dir / f"{subckt.upper()}.const.json"
            path.write_text(json.dumps(constraints, indent=2) + "\n")
        return design_dir

    def _argv(self, case: Case, design_dir: Path, run_dir: Path) -> list[str]:
        flow = case.flow
        argv = [
            self.align.python, str(self.align.entry_point),
            str(design_dir),
            "-f", str(design_dir / f"{case.subckt}.sp"),
            "-s", case.subckt,
            "-p", str(self.align.pdk(case.pdk)),
            "-w", str(run_dir),
            "-flat", "1" if flow.flat else "0",
            "-n", str(flow.nvariants),
            "-e", str(flow.effort),
            "--seed", str(flow.seed),
            "--router_mode", flow.router_mode,
            "-l", "INFO",
            "-v", "ERROR",
        ]
        if flow.placer_sa_iterations is not None:
            argv += ["--placer_sa_iterations", str(flow.placer_sa_iterations)]
        if flow.skip_gds:
            argv.append("--skipGDS")
        argv += list(flow.extra_args)
        return argv

    # -- artifact locations --------------------------------------------------

    @staticmethod
    def _layout_json(case: Case, run_dir: Path) -> Path:
        return run_dir / "3_pnr" / f"{case.subckt}_0.json"

    @staticmethod
    def _errors_file(case: Case, run_dir: Path) -> Path:
        return run_dir / "3_pnr" / f"{case.subckt}_0.errors"

    # -- interpretation ------------------------------------------------------

    def _classify(self, case: Case, run_dir: Path, invocation: Invocation) -> Status:
        """Attribute a failure to the furthest flow stage that produced output.

        ALIGN's stages are 1_topology, 2_primitives and 3_pnr (align/main.py),
        so the presence of each stage's completion artifact tells us where it
        stopped. This is what separates "ALIGN cannot build this primitive"
        from "ALIGN cannot route this circuit", which is the distinction the
        qualification exercise actually needs.
        """
        if invocation.timed_out:
            return Status.TIMEOUT
        if invocation.returncode < 0:
            return Status.CRASH

        if self._layout_json(case, run_dir).is_file():
            return Status.OK

        pnr_dir = run_dir / "3_pnr"
        if pnr_dir.is_dir() and any(pnr_dir.glob("*.placement_verilog.json")):
            return Status.ROUTE_FAIL

        primitives = run_dir / "2_primitives" / "__primitives__.json"
        if primitives.is_file():
            return Status.PLACE_FAIL
        if (run_dir / "2_primitives").is_dir():
            return Status.PRIMITIVE_FAIL

        if (run_dir / "1_topology" / f"{case.subckt}.verilog.json").is_file():
            return Status.PRIMITIVE_FAIL
        return Status.TOPOLOGY_FAIL

    def _collect_geometry(self, case: Case, run_dir: Path, result: RunResult) -> None:
        path = self._layout_json(case, run_dir)
        if not path.is_file():
            return
        try:
            layout = json.loads(path.read_text())
            x0, y0, x1, y1 = layout["bbox"]
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
            result.findings.append(Finding(
                category=Category.FLOW, code="unreadable_layout", severity=Severity.ERROR,
                message=f"could not read bbox from {path.name}: {exc}",
            ))
            return
        result.width_um = (x1 - x0) / LAYOUT_UNITS_PER_UM
        result.height_um = (y1 - y0) / LAYOUT_UNITS_PER_UM

    def _collect_flow_errors(self, case: Case, run_dir: Path, result: RunResult) -> None:
        """Parse ALIGN's self-reported error file into buckets.

        This is the flow's own grid-based check (align/cell_fabric/drc.py), not
        signoff DRC. It is recorded separately from signoff results because
        establishing whether the two agree is a Phase 1 deliverable.
        """
        path = self._errors_file(case, run_dir)
        if not path.is_file():
            if result.status is Status.OK:
                result.align_drc_errors = 0
            return

        counts: dict[str, int] = {bucket: 0 for bucket, _ in _ERROR_PATTERNS}
        counts["other"] = 0
        examples: dict[str, str] = {}

        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            bucket = next((name for name, pattern in _ERROR_PATTERNS if pattern.match(line)), "other")
            counts[bucket] += 1
            examples.setdefault(bucket, line)

        result.align_drc_errors = sum(counts.values())
        for bucket, count in counts.items():
            if not count:
                continue
            result.findings.append(Finding(
                category=Category.ROUTING if bucket in _ROUTING_BUCKETS else Category.DRC,
                code=bucket,
                severity=Severity.ERROR,
                message=f"{count} {bucket} violation(s) reported by ALIGN",
                location=str(path.relative_to(run_dir)),
                data={"count": count, "example": examples.get(bucket, "")},
            ))

    def _layout_hash(self, case: Case, run_dir: Path) -> str | None:
        path = self._layout_json(case, run_dir)
        if not path.is_file():
            return None
        try:
            layout = json.loads(path.read_text())
        except json.JSONDecodeError:
            return None
        return canonical_layout_hash(layout)


def canonical_layout_hash(layout: dict[str, Any]) -> str:
    """Digest of a layout, insensitive to the order shapes were emitted in.

    Used for determinism checks: two runs at the same seed should produce the
    same digest, and a digest that moves run to run is a bug rather than a
    characterization number.
    """
    terminals = sorted(
        (
            (str(t.get("layer", "")), str(t.get("netName", "")), tuple(t.get("rect", ())))
            for t in layout.get("terminals", [])
        )
    )
    payload = {"bbox": layout.get("bbox"), "terminals": terminals}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def _log_tail(path: Path, lines: int = 20) -> str:
    if not path.is_file():
        return ""
    return "\n".join(path.read_text(errors="replace").splitlines()[-lines:])


def _spawn(
    argv: list[str],
    cwd: Path,
    log_path: Path,
    timeout_s: int,
    env: dict[str, str] | None = None,
) -> Invocation:
    """Run argv to completion, capturing exit status, wall time and peak RSS.

    os.wait4 attributes peak RSS to this child alone; getrusage(RUSAGE_CHILDREN)
    would report a running maximum over every case the pytest worker has
    already run. The child gets its own process group so that a timeout kills
    the whole tree, including a solver that has stopped responding.
    """
    timed_out = False
    started = time.monotonic()

    with log_path.open("wb") as log:
        proc = subprocess.Popen(
            argv, cwd=str(cwd), stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True, env=env,
        )

        def _kill() -> None:
            nonlocal timed_out
            timed_out = True
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)

        timer = threading.Timer(timeout_s, _kill)
        timer.start()
        try:
            _, status, usage = os.wait4(proc.pid, 0)
        finally:
            timer.cancel()
        # Tell Popen the child is already reaped so it does not wait again.
        proc.returncode = os.waitstatus_to_exitcode(status)

    return Invocation(
        returncode=proc.returncode,
        timed_out=timed_out,
        peak_rss_mb=usage.ru_maxrss / _RSS_DIVISOR,
        wall_time_s=time.monotonic() - started,
    )
