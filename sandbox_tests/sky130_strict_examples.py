#!/usr/bin/env python3
import os
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
    examples_root = repo_root.parent / "ALIGN-pdk-sky130" / "examples"
    if not pdk_dir.is_dir():
        raise FileNotFoundError(f"Missing PDK dir: {pdk_dir}")
    if not examples_root.is_dir():
        raise FileNotFoundError(f"Missing examples dir: {examples_root}")

    strict_env = os.environ.copy()
    strict_env["ALIGN_SKY130_STRICT"] = "1"
    strict_env["ALIGN_SKY130_STRICT_RELAX_M4_PATHS"] = "0"
    strict_env["ALIGN_SKY130_STRICT_M4_BLOCK_GRID"] = "1"
    strict_env["ALIGN_SKY130_STRICT_CAPMIM_M4_ENC"] = "1"
    strict_env["ALIGN_PLACER_SKIP_SA"] = "0"
    strict_env["ALIGN_PLACER_SKIP_ENUM"] = "0"

    examples = [
        "buffer",
        "inverter",
        "five_transistor_ota",
        "current_mirror_ota",
        "telescopic_ota",
    ]

    tmp_root = repo_root / "sandbox_tests" / ".tmp"
    tmp_root.mkdir(parents=True, exist_ok=True)

    failures = []
    for name in examples:
        example_dir = examples_root / name
        if not example_dir.is_dir():
            print(f"skip missing example: {example_dir}")
            continue
        work_dir = Path(tempfile.mkdtemp(prefix=f"sky130_strict_{name}_", dir=tmp_root))
        cmd = [str(runner), str(example_dir), "-p", str(pdk_dir)]
        subprocess.run(cmd, cwd=work_dir, env=strict_env, check=True)
        errors_dir = work_dir / "3_pnr"
        bad = [err for err in errors_dir.glob("*.errors") if err.read_text().strip()]
        if bad:
            failures.append((name, work_dir, bad))

    if failures:
        details = ", ".join(f"{name}:{[str(b) for b in bad]}" for name, _, bad in failures)
        raise RuntimeError(f"Strict SKY130 example errors: {details}")

    print("sky130 strict examples: ok")


if __name__ == "__main__":
    main()
