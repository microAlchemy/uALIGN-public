#!/usr/bin/env python3
import importlib.util
import os
import pathlib

from align.compiler.read_library import read_models
from align.schema import Instance, SubCircuit
from align.schema.types import set_context


def main():
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    pdk_dir = repo_root.parent / "ALIGN-pdk-sky130" / "SKY130_PDK"
    assert pdk_dir.is_dir(), f"PDK dir not found: {pdk_dir}"

    os.environ["ALIGN_SKY130_ASSUME_UM"] = "1"

    parser = read_models(pdk_dir)
    lib = parser.library

    with set_context(lib):
        subckt = SubCircuit(name="SMOKE_CAP_UM", pins=["PLUS", "MINUS"], generator={"name": "CAP"})
    with set_context(subckt.elements):
        subckt.elements.append(
            Instance(
                name="C0",
                model="SKY130_FD_PR__CAP_MIM_M3_1",
                pins={"PLUS": "PLUS", "MINUS": "MINUS"},
                parameters={"W": "7", "L": "19"},
            )
        )
    lib.append(subckt)

    spec = importlib.util.spec_from_file_location("sky130_gen_param", pdk_dir / "gen_param.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    primitives = {}
    assert mod.gen_param(subckt, primitives, pdk_dir), "gen_param returned False for CAP um coercion"
    entry = primitives.get("SMOKE_CAP_UM")
    assert entry, "Missing CAP um primitive entry"
    assert entry["value"] == [19000, 7000], f"Unexpected CAP um value {entry['value']}"

    print("sky130 unit coercion: ok")


if __name__ == "__main__":
    main()
