"""LVS: does the layout match the netlist it was generated from?

The intended path reuses collateral already in the ALIGN checkout: magic
extracts a SPICE netlist from the GDS using ``benchmarks/magic_tech/<pdk>/
ext2spice.tcl``, and netgen compares it against the case's source netlist.
That is the same toolchain ALIGN's own post-layout simulation flow drives from
``benchmarks/scripts/run_extraction.sh``.

LVS status is deliberately four-valued. "Did not run" is not "clean", and
conflating them is how a suite comes to report a coverage number it has not
earned.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from ..case import Case
from ..paths import AlignInstall, tool_on_path
from ..result import Category, Finding, LvsStatus, RunResult, Severity

REQUIRED_TOOLS = ("magic", "netgen")


def missing_tools() -> list[str]:
    return [tool for tool in REQUIRED_TOOLS if not tool_on_path(tool)]


def magic_tech_dir(align: AlignInstall, pdk: str) -> Path | None:
    path = align.home / "benchmarks" / "magic_tech" / pdk
    return path if path.is_dir() else None


def gds_path(result: RunResult, case: Case) -> Path | None:
    if not result.artifacts_dir:
        return None
    all_gds = sorted((Path(result.artifacts_dir) / "run").rglob(f"{case.subckt}_0.gds"))
    if not all_gds:
        all_gds = sorted((Path(result.artifacts_dir) / "run").rglob("*.gds"))
        all_gds = [p for p in all_gds if not p.name.endswith(".python.gds")]
    return all_gds[0] if all_gds else None


def check(case: Case, result: RunResult, align: AlignInstall) -> tuple[LvsStatus, list[Finding]]:
    missing = missing_tools()
    if missing:
        return LvsStatus.NOT_RUN, [Finding(
            category=Category.LVS, code="lvs_not_run", severity=Severity.INFO,
            message=f"missing tools on PATH: {', '.join(missing)}",
        )]

    if case.flow.skip_gds:
        return LvsStatus.NOT_RUN, [Finding(
            category=Category.LVS, code="lvs_not_run", severity=Severity.WARNING,
            message="case sets flow.skip_gds, so there is nothing to extract",
        )]

    gds = gds_path(result, case)
    if gds is None:
        return LvsStatus.NOT_RUN, [Finding(
            category=Category.LVS, code="lvs_not_run", severity=Severity.WARNING,
            message="no GDS found in the run artifacts",
        )]

    tech_dir = magic_tech_dir(align, case.pdk)
    if tech_dir is None:
        return LvsStatus.NOT_RUN, [Finding(
            category=Category.LVS, code="no_tech_file", severity=Severity.INFO,
            message=f"no magic tech collateral for PDK '{case.pdk}' in benchmarks/magic_tech",
        )]

    tech_files = list(tech_dir.glob("*.tech"))
    tcl_files = list(tech_dir.glob("ext2spice.tcl"))
    if not tech_files or not tcl_files:
        return LvsStatus.NOT_RUN, [Finding(
            category=Category.LVS, code="no_tech_file", severity=Severity.INFO,
            message="missing .tech or ext2spice.tcl in benchmarks/magic_tech",
        )]

    run_dir = Path(result.artifacts_dir or ".") / "run"
    extracted_spice = run_dir / "extracted.spice"

    # Step 1: Run Magic Extraction
    env = os.environ.copy()
    env["INPUT_GDS"] = str(gds.resolve())
    env["OUTPUT_DIR"] = str(run_dir.resolve())
    env["CIRCUIT"] = case.subckt

    try:
        with open(tcl_files[0], "r") as tcl_in:
            proc_ext = subprocess.run(
                ["magic", "-dnull", "-noconsole", "-T", str(tech_files[0].resolve())],
                stdin=tcl_in,
                capture_output=True,
                text=True,
                timeout=180,
                cwd=str(run_dir),
                env=env,
            )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return LvsStatus.ERROR, [Finding(
            category=Category.LVS, code="extraction_error", severity=Severity.ERROR,
            message=f"magic extraction failed: {exc}",
        )]

    if not extracted_spice.is_file():
        return LvsStatus.ERROR, [Finding(
            category=Category.LVS, code="missing_extracted_spice", severity=Severity.ERROR,
            message="magic did not produce extracted.spice",
            data={"stdout_tail": proc_ext.stdout[-500:], "stderr_tail": proc_ext.stderr[-500:]},
        )]

    # Step 2: Run Netgen LVS
    design_sp = Path(result.artifacts_dir or ".") / "design" / f"{case.subckt}.sp"
    if not design_sp.is_file():
        return LvsStatus.ERROR, [Finding(
            category=Category.LVS, code="missing_design_spice", severity=Severity.ERROR,
            message=f"design netlist {design_sp.name} not found",
        )]

    comp_out = run_dir / "lvs_comp.out"
    try:
        proc_lvs = subprocess.run(
            ["netgen", "-batch", "lvs",
             f"{extracted_spice.name} {case.subckt}",
             f"{design_sp.resolve()} {case.subckt}",
             str(comp_out.resolve())],
            capture_output=True,
            text=True,
            timeout=180,
            cwd=str(run_dir),
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return LvsStatus.ERROR, [Finding(
            category=Category.LVS, code="netgen_error", severity=Severity.ERROR,
            message=f"netgen LVS failed: {exc}",
        )]

    comp_text = comp_out.read_text(errors="replace") if comp_out.is_file() else proc_lvs.stdout

    if "Circuits match uniquely" in comp_text:
        return LvsStatus.CLEAN, [Finding(
            category=Category.LVS, code="lvs_clean", severity=Severity.INFO,
            message="LVS clean: circuits match uniquely",
        )]
    elif "Netlists do not match" in comp_text or "Circuits do not match" in comp_text:
        return LvsStatus.MISMATCH, [Finding(
            category=Category.LVS, code="lvs_mismatch", severity=Severity.ERROR,
            message="LVS mismatch: extracted layout netlist does not match schematic netlist",
            data={"tail": comp_text[-1000:]},
        )]
    else:
        return LvsStatus.ERROR, [Finding(
            category=Category.LVS, code="lvs_unknown_output", severity=Severity.ERROR,
            message="netgen finished with inconclusive output; check lvs_comp.out",
            data={"tail": comp_text[-1000:]},
        )]
