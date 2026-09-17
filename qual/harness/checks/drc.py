"""Signoff DRC, run by an external tool.

ALIGN's own error count comes from a grid-based check against its abstracted
PDK (``align/cell_fabric/drc.py``), not from a signoff deck. Whether "0 ALIGN
errors" implies "0 signoff errors" is a question this suite exists to answer,
so the two counts are recorded in separate columns and never merged.

The ALIGN checkout already carries magic tech files and ext2spice scripts for
FinFET14nm_Mock_PDK and SKY130_PDK under ``benchmarks/magic_tech/``, which is
the shortest path to a real deck. Reuse them rather than writing new ones.

Signoff DRC needs GDS, so a case being checked must not set
``flow.skip_gds``.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

from ..case import Case
from ..paths import AlignInstall, tool_on_path
from ..result import Category, Finding, RunResult, Severity

# In preference order: a real deck beats a mock one.
CANDIDATE_TOOLS = ("magic", "klayout", "calibre")


def available_tool() -> str | None:
    return next((tool for tool in CANDIDATE_TOOLS if tool_on_path(tool)), None)


def magic_tech_dir(align: AlignInstall, pdk: str) -> Path | None:
    """Magic tech collateral shipped with ALIGN, if it covers this PDK."""
    path = align.home / "benchmarks" / "magic_tech" / pdk
    return path if path.is_dir() else None


def gds_path(result: RunResult, case: Case) -> Path | None:
    if not result.artifacts_dir:
        return None
    # Prefer plain .gds over .python.gds
    all_gds = sorted((Path(result.artifacts_dir) / "run").rglob(f"{case.subckt}_0.gds"))
    if not all_gds:
        all_gds = sorted((Path(result.artifacts_dir) / "run").rglob("*.gds"))
        all_gds = [p for p in all_gds if not p.name.endswith(".python.gds")]
    return all_gds[0] if all_gds else None


def check(case: Case, result: RunResult, align: AlignInstall) -> tuple[int | None, list[Finding]]:
    """Return (signoff error count, findings). Count is None when not run."""
    tool = available_tool()
    if tool is None:
        return None, [Finding(
            category=Category.DRC, code="drc_not_run", severity=Severity.INFO,
            message=f"no signoff DRC tool on PATH (looked for {', '.join(CANDIDATE_TOOLS)})",
        )]

    if case.flow.skip_gds:
        return None, [Finding(
            category=Category.DRC, code="drc_not_run", severity=Severity.WARNING,
            message="case sets flow.skip_gds, so there is no layout for signoff DRC to read",
        )]

    gds = gds_path(result, case)
    if gds is None:
        return None, [Finding(
            category=Category.DRC, code="drc_not_run", severity=Severity.WARNING,
            message="no GDS found in the run artifacts",
        )]

    if tool == "magic":
        return _run_magic_drc(case, gds, align)

    return None, [Finding(
        category=Category.DRC, code="not_implemented", severity=Severity.WARNING,
        message=f"signoff DRC via {tool} is not wired up yet",
        data={"tool": tool, "gds": str(gds)},
    )]


def _run_magic_drc(case: Case, gds: Path, align: AlignInstall) -> tuple[int | None, list[Finding]]:
    tech_dir = magic_tech_dir(align, case.pdk)
    if tech_dir is None:
        return None, [Finding(
            category=Category.DRC, code="no_tech_file", severity=Severity.INFO,
            message=f"no magic tech collateral for PDK '{case.pdk}' in benchmarks/magic_tech",
        )]

    tech_files = list(tech_dir.glob("*.tech"))
    if not tech_files:
        return None, [Finding(
            category=Category.DRC, code="no_tech_file", severity=Severity.INFO,
            message=f"no *.tech file found in {tech_dir}",
        )]
    tech_file = tech_files[0]

    tcl_script = f"""
gds readonly true
gds read {gds.resolve()}
set cells [cellname list allcells]
set circuit_upper [string toupper "{case.subckt}"]
set topcell ""
foreach cell $cells {{
    if {{[string match "${{circuit_upper}}*" $cell]}} {{
        set topcell $cell
        break
    }}
}}
if {{$topcell eq ""}} {{
    set topcell [lindex $cells 0]
}}
load $topcell
select top cell
drc check
drc catchup
set drc_count [drc list count total]
puts "SIGNOFF_DRC_COUNT: $drc_count"
quit
"""
    with tempfile.NamedTemporaryFile("w", suffix=".tcl", delete=False) as script_file:
        script_file.write(tcl_script)
        script_path = Path(script_file.name)

    try:
        proc = subprocess.run(
            ["magic", "-dnull", "-noconsole", "-T", str(tech_file.resolve())],
            input=tcl_script,
            text=True,
            capture_output=True,
            timeout=180,
            cwd=str(gds.parent),
        )
    except subprocess.TimeoutExpired:
        return None, [Finding(
            category=Category.DRC, code="drc_timeout", severity=Severity.ERROR,
            message="magic DRC timed out after 180s",
        )]
    except OSError as exc:
        return None, [Finding(
            category=Category.DRC, code="drc_error", severity=Severity.ERROR,
            message=f"failed to run magic DRC: {exc}",
        )]
    finally:
        script_path.unlink(missing_ok=True)

    match = re.search(r"SIGNOFF_DRC_COUNT:\s*(\d+)", proc.stdout)
    if not match:
        return None, [Finding(
            category=Category.DRC, code="drc_parse_error", severity=Severity.WARNING,
            message="could not parse signoff DRC error count from magic output",
            data={"stdout_tail": proc.stdout[-500:], "stderr_tail": proc.stderr[-500:]},
        )]

    count = int(match.group(1))
    findings = [Finding(
        category=Category.DRC,
        code="signoff_drc",
        severity=Severity.ERROR if count > 0 else Severity.INFO,
        message=f"magic signoff DRC reported {count} violation(s)",
        data={"count": count, "tech": str(tech_file.name)},
    )]
    return count, findings
