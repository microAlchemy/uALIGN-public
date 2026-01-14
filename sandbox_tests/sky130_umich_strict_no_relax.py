#!/usr/bin/env python3
import os
import json
import subprocess
import tempfile
from pathlib import Path


def main():
    repo_root = Path(__file__).resolve().parents[1]
    venv_bin = repo_root / ".venv_sky130" / "bin"
    runner = venv_bin / "schematic2layout.py"
    if not runner.exists():
        raise FileNotFoundError(f"Missing venv runner: {runner}")

    pdk_dir = repo_root.parent / "ALIGN-pdk-sky130" / "SKY130_PDK"
    example_dir = repo_root.parent / "ALIGN-pdk-sky130" / "examples" / "umich_test_case"
    if not pdk_dir.is_dir():
        raise FileNotFoundError(f"Missing PDK dir: {pdk_dir}")
    if not example_dir.is_dir():
        raise FileNotFoundError(f"Missing example dir: {example_dir}")

    tmp_root = repo_root / "sandbox_tests" / ".tmp"
    tmp_root.mkdir(parents=True, exist_ok=True)
    work_dir = Path(tempfile.mkdtemp(prefix="sky130_umich_strict_", dir=tmp_root))

    env = os.environ.copy()
    env["ALIGN_SKY130_STRICT"] = "1"
    env["ALIGN_SKY130_STRICT_RELAX_M4_PATHS"] = "0"
    env["ALIGN_SKY130_STRICT_M4_BLOCK_GRID"] = "1"
    env["ALIGN_PLACER_SKIP_SA"] = "0"
    env["ALIGN_PLACER_SKIP_ENUM"] = "0"

    cmd = [str(runner), str(example_dir), "-p", str(pdk_dir)]
    subprocess.run(cmd, cwd=work_dir, env=env, check=True)

    errors_dir = work_dir / "3_pnr"
    if not errors_dir.is_dir():
        raise FileNotFoundError(f"Missing output dir: {errors_dir}")

    bad = []
    for err_file in errors_dir.glob("*.errors"):
        if err_file.read_text().strip():
            bad.append(err_file)

    if bad:
        raise RuntimeError(f"Strict UMICH errors found: {bad}")

    layers_json = pdk_dir / "layers.json"
    with layers_json.open() as fp:
        layers = json.load(fp)
    m4 = next(layer for layer in layers["Abstraction"] if layer["Layer"] == "M4")
    m4_pitch = int(m4["Pitch"])
    m4_offset = int(m4.get("Offset", 0))

    placement_file = work_dir / "3_pnr" / "Results" / "UMICH_TEST_CASE_0.scaled_placement_verilog.json"
    if not placement_file.is_file():
        matches = list((work_dir / "3_pnr" / "Results").glob("*scaled_placement_verilog.json"))
        raise FileNotFoundError(f"Missing placement json: {placement_file} (found {matches})")

    with placement_file.open() as fp:
        placement = json.load(fp)

    modules = placement.get("modules", [])
    top_module = next((m for m in modules if m.get("concrete_name") == "UMICH_TEST_CASE_0"), None)
    if top_module is None:
        raise RuntimeError(f"Top module UMICH_TEST_CASE_0 not found in {placement_file}")

    offgrid = []
    for inst in top_module.get("instances", []):
        oy = int(inst["transformation"]["oY"])
        if (oy - m4_offset) % m4_pitch != 0:
            offgrid.append((top_module["concrete_name"], inst["instance_name"], oy))

    if offgrid:
        raise RuntimeError(f"M4 block grid violations found: {offgrid[:8]}")

    print("sky130 umich strict no-relax + m4 block grid: ok")


if __name__ == "__main__":
    main()
