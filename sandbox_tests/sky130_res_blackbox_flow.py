#!/usr/bin/env python3
import os
import subprocess
import tempfile
from pathlib import Path


def main():
    repo_root = Path(__file__).resolve().parents[1]
    venv_bin = repo_root / ".venv_sky130" / "bin"
    runner = venv_bin / "schematic2layout.py"
    bb_prep = repo_root / "bin" / "sky130_res_blackbox_prep.py"
    python_exec = venv_bin / "python"
    if not python_exec.exists():
        python_exec = venv_bin / "python3"

    if not runner.exists():
        raise FileNotFoundError(f"Missing venv runner: {runner}")
    if not python_exec.exists():
        raise FileNotFoundError("Missing venv python executable")
    if not bb_prep.exists():
        raise FileNotFoundError(f"Missing blackbox prep script: {bb_prep}")

    pdk_dir = repo_root.parent / "ALIGN-pdk-sky130" / "SKY130_PDK"
    if not pdk_dir.is_dir():
        raise FileNotFoundError(f"Missing PDK dir: {pdk_dir}")

    tmp_root = repo_root / "sandbox_tests" / ".tmp"
    tmp_root.mkdir(parents=True, exist_ok=True)
    work_dir = Path(tempfile.mkdtemp(prefix="sky130_res_bb_", dir=tmp_root))

    netlist_dir = work_dir / "netlist"
    netlist_dir.mkdir(parents=True, exist_ok=True)
    netlist_path = netlist_dir / "resbb.sp"
    netlist_path.write_text(
        ".SUBCKT RESBB A B\n"
        "R1 A B RESISTOR R=1000\n"
        ".ENDS RESBB\n"
    )

    bb_dir = work_dir / "blackbox"
    env = os.environ.copy()
    env["ALIGN_SKY130_RES_BLACKBOX"] = "1"

    cmd = [
        str(python_exec),
        str(bb_prep),
        str(netlist_dir),
        "-p",
        str(pdk_dir),
        "--work-dir",
        str(work_dir),
        "-o",
        str(bb_dir),
    ]
    subprocess.run(cmd, env=env, check=True)

    gds_files = list(bb_dir.glob("*.gds"))
    assert gds_files, f"No blackbox GDS produced in {bb_dir}"

    cmd = [str(runner), str(netlist_dir), "-p", str(pdk_dir), "-b", str(bb_dir)]
    subprocess.run(cmd, cwd=work_dir, env=env, check=True)

    prim_dir = work_dir / "2_primitives"
    for gds in gds_files:
        lef = prim_dir / (gds.stem + ".lef")
        assert lef.exists(), f"Missing LEF for {gds.stem} in {prim_dir}"

    print("sky130 res blackbox flow: ok")


if __name__ == "__main__":
    main()
