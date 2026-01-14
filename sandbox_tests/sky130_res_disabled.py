#!/usr/bin/env python3
import os
import pathlib

from align.compiler.gen_abstract_name import PrimitiveLibrary
from align.compiler.read_library import read_models
from align.schema import Instance, SubCircuit
from align.schema.types import set_context


def main():
    os.environ.pop("ALIGN_SKY130_ENABLE_RES", None)

    repo_root = pathlib.Path(__file__).resolve().parents[1]
    pdk_dir = repo_root.parent / "ALIGN-pdk-sky130" / "SKY130_PDK"
    assert pdk_dir.is_dir(), f"PDK dir not found: {pdk_dir}"

    parser = read_models(pdk_dir)
    lib = parser.library

    with set_context(lib):
        subckt = SubCircuit(name="SMOKE_RES_DISABLE", pins=["PLUS", "MINUS"])
    with set_context(subckt.elements):
        subckt.elements.append(
            Instance(
                name="R1",
                model="RESISTOR",
                pins={"PLUS": "PLUS", "MINUS": "MINUS"},
                parameters={"R": "1000", "M": "2"},
            )
        )
    lib.append(subckt)

    try:
        PrimitiveLibrary(lib, pdk_dir).gen_primitive_collateral()
    except NotImplementedError as exc:
        msg = str(exc)
        assert "SKY130 resistor generator is not DRC-clean yet" in msg, f"Unexpected error: {msg}"
    else:
        raise AssertionError("Expected NotImplementedError for SKY130 resistor generator")

    print("sky130 res disabled: ok")


if __name__ == "__main__":
    main()
