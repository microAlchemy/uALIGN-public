#!/usr/bin/env python3
import pathlib

from align.compiler.read_library import read_models


def main():
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    pdk_dir = repo_root.parent / "ALIGN-pdk-sky130" / "SKY130_PDK"
    assert pdk_dir.is_dir(), f"PDK dir not found: {pdk_dir}"

    parser = read_models(pdk_dir)
    try:
        parser.parse(
            ".subckt TOP A B C\n"
            "X1 A B C sky130_fd_pr__res_generic_nd L=1e-6 W=1e-6\n"
            ".ends TOP\n"
        )
    except NotImplementedError as exc:
        msg = str(exc)
        assert "3-terminal SKY130 resistors are unsupported" in msg, f"Unexpected error: {msg}"
    else:
        raise AssertionError("Expected NotImplementedError for 3-terminal SKY130 resistor")

    print("sky130 3-pin resistor unsupported: ok")


if __name__ == "__main__":
    main()
