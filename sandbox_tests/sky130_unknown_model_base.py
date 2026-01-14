#!/usr/bin/env python3
import pathlib

from align.compiler.read_library import read_models


def main():
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    pdk_dir = repo_root.parent / "ALIGN-pdk-sky130" / "SKY130_PDK"
    assert pdk_dir.is_dir(), f"PDK dir not found: {pdk_dir}"

    parser = read_models(pdk_dir)
    parser.parse(
        ".subckt TOP D G S B\n"
        "M1 D G S B sky130_fd_pr__nfet_01v8_lvt W=1.0e-6 L=0.15e-6\n"
        ".ends TOP\n"
    )

    model = parser.library.find("SKY130_FD_PR__NFET_01V8_LVT")
    assert model is not None, "Missing inferred model for sky130_fd_pr__nfet_01v8_lvt"
    assert model.base == "NMOS", f"Expected base NMOS, got {model.base}"

    print("sky130 unknown model base inference: ok")


if __name__ == "__main__":
    main()
