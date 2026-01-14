#!/usr/bin/env python3
import pathlib

from align.compiler.gen_abstract_name import PrimitiveLibrary
from align.compiler.read_library import read_models


def main():
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    pdk_dir = repo_root.parent / "ALIGN-pdk-sky130" / "SKY130_PDK"
    assert pdk_dir.is_dir(), f"PDK dir not found: {pdk_dir}"

    parser = read_models(pdk_dir)
    parser.parse(
        ".subckt TOP A B\n"
        "D1 A B sky130_fd_pr__diode_pw2nd_05v5\n"
        ".ends TOP\n"
    )

    model = parser.library.find("SKY130_FD_PR__DIODE_PW2ND_05V5")
    assert model is not None, "Missing inferred model for sky130_fd_pr__diode_pw2nd_05v5"
    assert model.base == "DIODE", f"Expected base DIODE, got {model.base}"

    try:
        PrimitiveLibrary(parser.library, pdk_dir).gen_primitive_collateral()
    except NotImplementedError as exc:
        msg = str(exc)
        assert "Diodes are unsupported" in msg, f"Unexpected error: {msg}"
    else:
        raise AssertionError("Expected NotImplementedError for unsupported diode")

    print("sky130 unsupported diode: ok")


if __name__ == "__main__":
    main()
