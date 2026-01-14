#!/usr/bin/env python3
import importlib.util
import pathlib

from align.cell_fabric.pdk import Pdk
from align.compiler.read_library import read_models
from align.compiler.util import get_generator
from align.schema import Instance, SubCircuit
from align.schema.types import set_context


def main():
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    pdk_dir = repo_root.parent / "ALIGN-pdk-sky130" / "SKY130_PDK"
    assert pdk_dir.is_dir(), f"PDK dir not found: {pdk_dir}"

    Pdk().load(pdk_dir / "layers.json")

    assert get_generator("sky130_fd_pr__nfet_01v8", pdk_dir), "Missing nfet generator mapping"
    assert get_generator("sky130_fd_pr__pfet_01v8", pdk_dir), "Missing pfet generator mapping"
    assert get_generator("sky130_fd_pr__cap_mim_m3_1", pdk_dir), "Missing cap generator mapping"
    assert get_generator("resistor", pdk_dir), "Missing res generator mapping"

    parser = read_models(pdk_dir)
    lib = parser.library

    with set_context(lib):
        subckt = SubCircuit(name="SMOKE_NMOS", pins=["D", "G", "S", "B"], generator={"name": "MOS"})
    with set_context(subckt.elements):
        subckt.elements.append(
            Instance(
                name="M1",
                model="SKY130_FD_PR__NFET_01V8",
                pins={"D": "D", "G": "G", "S": "S", "B": "B"},
                parameters={"W": "1.0e-6", "L": "0.15e-6", "NF": "1", "M": "1"},
            )
        )
    lib.append(subckt)

    spec = importlib.util.spec_from_file_location("sky130_gen_param", pdk_dir / "gen_param.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    primitives = {}
    assert mod.gen_param(subckt, primitives, pdk_dir), "gen_param returned False"
    assert primitives, "gen_param produced no primitives"

    print("sky130 smoke tests: ok")


if __name__ == "__main__":
    main()
