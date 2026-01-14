#!/usr/bin/env python3
import pathlib

from align.compiler.gen_abstract_name import PrimitiveLibrary
from align.compiler.read_library import read_models
from align.schema import Instance, SubCircuit
from align.schema.types import set_context


def main():
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    pdk_dir = repo_root.parent / "ALIGN-pdk-sky130" / "SKY130_PDK"
    assert pdk_dir.is_dir(), f"PDK dir not found: {pdk_dir}"

    parser = read_models(pdk_dir)
    lib = parser.library

    with set_context(lib):
        subckt = SubCircuit(name="SMOKE_IND", pins=["PLUS", "MINUS"])
    with set_context(subckt.elements):
        subckt.elements.append(
            Instance(
                name="L1",
                model="INDUCTOR",
                pins={"PLUS": "PLUS", "MINUS": "MINUS"},
                parameters={"VALUE": "1e-9"},
            )
        )
    lib.append(subckt)

    try:
        PrimitiveLibrary(lib, pdk_dir).gen_primitive_collateral()
    except NotImplementedError as exc:
        msg = str(exc)
        assert "Inductors are unsupported" in msg, f"Unexpected error: {msg}"
    else:
        raise AssertionError("Expected NotImplementedError for unsupported inductor")

    print("sky130 unsupported inductor: ok")


if __name__ == "__main__":
    main()
