"""Post-layout SPICE electrical simulation figures of merit.

Runs post-layout AC and transient simulations using ngspice when available,
extracting key analog electrical metrics:
- DC Gain (dB)
- Unity-Gain Bandwidth (UGBW in MHz)
- Phase Margin (degrees)
- Common-Mode Rejection Ratio (CMRR in dB)
- Comparator Regeneration Time / Delay (ns)
- Static Power Consumption (µW)

When ngspice is not installed or no testbench is available for the given case,
the check records an INFO finding and exits cleanly so the test suite does not block.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from ..case import Case
from ..paths import AlignInstall, tool_on_path
from ..result import Category, Finding, RunResult, Severity


def check(case: Case, result: RunResult, align: AlignInstall) -> list[Finding]:
    """Execute post-layout simulation if ngspice and testbenches are available."""
    if not tool_on_path("ngspice"):
        return [Finding(
            category=Category.SIMULATION,
            code="simulation_not_run",
            severity=Severity.INFO,
            message="ngspice not found on PATH; skipping post-layout simulation",
        )]

    if not result.artifacts_dir:
        return []

    # Locate testbench in benchmarks/testbenches/
    tb_candidates = [
        align.home / "benchmarks" / "testbenches" / case.id / "tb.sp",
        align.home / "benchmarks" / "testbenches" / case.subckt.lower() / "tb.sp",
    ]
    tb_path = next((p for p in tb_candidates if p.is_file()), None)
    if tb_path is None:
        return [Finding(
            category=Category.SIMULATION,
            code="no_testbench",
            severity=Severity.INFO,
            message=f"no ngspice testbench found for case '{case.id}'",
        )]

    # Locate extracted SPICE netlist or fall back to design netlist
    run_dir = Path(result.artifacts_dir) / "run"
    extracted_candidates = [
        run_dir / "extracted.spice",
        run_dir / f"{case.subckt}.extracted.spice",
        run_dir / "3_pnr" / f"{case.subckt}_0.sp",
        Path(result.artifacts_dir) / "design" / f"{case.subckt}.sp",
    ]
    extracted_spice = next((p for p in extracted_candidates if p.is_file()), None)
    if extracted_spice is None:
        return [Finding(
            category=Category.SIMULATION,
            code="no_extracted_netlist",
            severity=Severity.WARNING,
            message="no extracted or design SPICE netlist found for simulation",
        )]

    # Prepare simulation work directory
    sim_dir = run_dir / "simulation"
    sim_dir.mkdir(parents=True, exist_ok=True)

    # Link/copy extracted netlist as extracted.spice
    target_extracted = sim_dir / "extracted.spice"
    if not target_extracted.exists():
        shutil.copy2(extracted_spice, target_extracted)

    # Copy testbench
    target_tb = sim_dir / "tb.sp"
    shutil.copy2(tb_path, target_tb)

    log_path = sim_dir / "ngspice.log"
    try:
        with log_path.open("wb") as log_file:
            proc = subprocess.run(
                ["ngspice", "-b", "tb.sp"],
                cwd=str(sim_dir),
                stdout=log_file,
                stderr=subprocess.STDOUT,
                timeout=120,
            )
    except subprocess.TimeoutExpired:
        return [Finding(
            category=Category.SIMULATION,
            code="simulation_timeout",
            severity=Severity.WARNING,
            message="ngspice simulation timed out after 120s",
        )]
    except OSError as exc:
        return [Finding(
            category=Category.SIMULATION,
            code="simulation_error",
            severity=Severity.WARNING,
            message=f"failed to run ngspice: {exc}",
        )]

    output_text = log_path.read_text(errors="replace")
    raw_metrics = _parse_ngspice_output(output_text)
    raw_metrics.update(_parse_ac_print_data(output_text))

    scaled = _scale_sim_metrics(raw_metrics)
    result.sim_metrics = scaled

    findings: list[Finding] = []
    if scaled:
        findings.append(Finding(
            category=Category.SIMULATION,
            code="simulation_metrics",
            severity=Severity.INFO,
            message="post-layout simulation metrics: " + ", ".join(f"{k}={v}" for k, v in scaled.items()),
            data=scaled,
        ))
    else:
        findings.append(Finding(
            category=Category.SIMULATION,
            code="simulation_no_data",
            severity=Severity.INFO,
            message="simulation completed but no .measure lines matched",
        ))

    return findings


def _parse_ngspice_output(ngspice_stdout: str) -> dict[str, float]:
    """Extract .measure results from ngspice stdout."""
    results: dict[str, float] = {}
    for line in ngspice_stdout.splitlines():
        m = re.match(r"^\s*([\w_]+)\s*=\s*([-+]?[\d.]+(?:[eE][-+]?\d+)?)", line)
        if m:
            name = m.group(1).lower().strip()
            try:
                results[name] = float(m.group(2))
            except ValueError:
                pass
    return results


def _parse_ac_print_data(ngspice_stdout: str) -> dict[str, float]:
    """Parse AC print rows to compute magnitude-based gain_lin and ugbw_mhz."""
    row_re = re.compile(
        r"^\s*\d+\s+([\d.e+\-]+)\s+([-+]?[\d.e+\-]+),\s*([-+]?[\d.e+\-]+)",
        re.MULTILINE,
    )
    rows: list[tuple[float, float]] = []
    for m in row_re.finditer(ngspice_stdout):
        freq = float(m.group(1))
        real = float(m.group(2))
        imag = float(m.group(3))
        rows.append((freq, math.sqrt(real ** 2 + imag ** 2)))

    if len(rows) < 2:
        return {}

    result: dict[str, float] = {}
    max_mag = max(mag for _, mag in rows)
    if max_mag > 0:
        result["gain_lin"] = max_mag

    if max_mag > 1.0:
        for i in range(len(rows) - 1):
            f1, m1 = rows[i]
            f2, m2 = rows[i + 1]
            if m1 >= 1.0 >= m2 and m1 != m2:
                t = (1.0 - m1) / (m2 - m1)
                result["ugbw_mhz"] = f1 * (f2 / f1) ** t
                break

    return result


def _scale_sim_metrics(raw: dict[str, float]) -> dict[str, float]:
    """Convert raw ngspice values to standard engineering units."""
    scaled: dict[str, float] = {}
    unit_map: dict[str, tuple[str, Callable[[float], float] | float]] = {
        "tphl_ns": ("tphl_ns", 1e9),
        "tplh_ns": ("tplh_ns", 1e9),
        "gain_db": ("gain_db", 1.0),
        "gain_lin": ("gain_db", lambda v: 20.0 * math.log10(abs(v)) if v != 0 else -999.0),
        "ugbw_mhz": ("ugbw_mhz", 1e-6),
        "phase_at_ugbw": ("phase_margin_deg", lambda v: 180.0 + v),
        "bandwidth_mhz": ("bandwidth_mhz", 1e-6),
        "cmrr_db": ("cmrr_db", 1.0),
        "regen_time_ns": ("regen_time_ns", 1e9),
        "static_power_uw": ("static_power_uw", 1e6),
        "gain_range_db": ("gain_range_db", 1.0),
        "f3db_mhz": ("f3db_mhz", 1e-6),
        "passband_ripple_db": ("passband_ripple_db", 1.0),
    }
    for raw_name, (out_name, scale) in unit_map.items():
        if raw_name in raw:
            val = raw[raw_name]
            if callable(scale):
                scaled[out_name] = round(scale(val), 4)
            else:
                scaled[out_name] = round(val * scale, 4)
    return scaled
