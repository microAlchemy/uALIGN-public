#!/usr/bin/env python3
import importlib.util
import json
import math
import pathlib

from align.compiler.read_library import read_models
from align.schema import Instance, SubCircuit
from align.schema.types import set_context


def main():
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    pdk_dir = repo_root.parent / "ALIGN-pdk-sky130" / "SKY130_PDK"
    assert pdk_dir.is_dir(), f"PDK dir not found: {pdk_dir}"

    with open(pdk_dir / "layers.json", "rt") as fp:
        unit_height_res = json.load(fp)["design_info"]["unit_height_res"]

    parser = read_models(pdk_dir)
    lib = parser.library

    with set_context(lib):
        subckt = SubCircuit(name="SMOKE_RES_MULT", pins=["PLUS", "MINUS"], generator={"name": "RES"})
    with set_context(subckt.elements):
        subckt.elements.append(
            Instance(
                name="R1",
                model="SKY130_FD_PR__RES_GENERIC_ND",
                pins={"PLUS": "PLUS", "MINUS": "MINUS"},
                parameters={"R": "1000", "M": "2"},
            )
        )
    lib.append(subckt)

    spec = importlib.util.spec_from_file_location("sky130_gen_param", pdk_dir / "gen_param.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    primitives = {}
    assert mod.gen_param(subckt, primitives, pdk_dir), "gen_param returned False for RES mult"
    entry = primitives.get("SMOKE_RES_MULT")
    assert entry, "Missing RES mult primitive entry"
    assert entry.get("primitive") == "RES", f"Unexpected RES primitive {entry}"
    height, size = entry["value"]
    expected_size = 500.0
    expected_height = math.ceil(math.sqrt(expected_size / unit_height_res))
    assert height == expected_height, f"Unexpected height {height} (expected {expected_height})"
    assert abs(float(size) - expected_size) < 1e-6, f"Unexpected size {size} (expected {expected_size})"

    print("sky130 res mult smoke: ok")


if __name__ == "__main__":
    main()
