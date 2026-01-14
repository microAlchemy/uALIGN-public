#!/usr/bin/env python3
import importlib.util
import math
import pathlib

from align.compiler.read_library import read_models
from align.schema import Instance, SubCircuit
from align.schema.types import set_context


def _build_cap_subckt(lib, name, params, model="SKY130_FD_PR__CAP_MIM_M3_1"):
    with set_context(lib):
        subckt = SubCircuit(name=name, pins=["PLUS", "MINUS"], generator={"name": "CAP"})
    with set_context(subckt.elements):
        subckt.elements.append(
            Instance(
                name="C0",
                model=model,
                pins={"PLUS": "PLUS", "MINUS": "MINUS"},
                parameters=params,
            )
        )
    lib.append(subckt)
    return subckt


def main():
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    pdk_dir = repo_root.parent / "ALIGN-pdk-sky130" / "SKY130_PDK"
    assert pdk_dir.is_dir(), f"PDK dir not found: {pdk_dir}"

    parser = read_models(pdk_dir)
    lib = parser.library

    subckt_lw = _build_cap_subckt(
        lib,
        "SMOKE_CAP_LW",
        {"L": "1.9e-6", "W": "70e-9"},
    )
    subckt_area = _build_cap_subckt(
        lib,
        "SMOKE_CAP_AREA",
        {"AREA": "4e-12", "MF": "2"},
    )

    spec = importlib.util.spec_from_file_location("sky130_gen_param", pdk_dir / "gen_param.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    primitives = {}
    assert mod.gen_param(subckt_lw, primitives, pdk_dir), "gen_param returned False for CAP L/W"
    entry = primitives.get("SMOKE_CAP_LW")
    assert entry, "Missing CAP L/W primitive entry"
    assert entry.get("primitive") == "CAP", f"Unexpected CAP primitive {entry}"
    assert entry["value"] == [1900, 70], f"Unexpected CAP L/W value {entry['value']}"

    primitives_area = {}
    assert mod.gen_param(subckt_area, primitives_area, pdk_dir), "gen_param returned False for CAP AREA"
    entry_area = primitives_area.get("SMOKE_CAP_AREA")
    assert entry_area, "Missing CAP AREA primitive entry"
    assert entry_area.get("primitive") == "CAP", f"Unexpected CAP AREA primitive {entry_area}"
    side = int(round(math.sqrt(4e-12 * 1e18), 4))
    assert entry_area["value"] == [side, side], f"Unexpected CAP AREA value {entry_area['value']}"

    print("sky130 cap variant smoke: ok")


if __name__ == "__main__":
    main()
