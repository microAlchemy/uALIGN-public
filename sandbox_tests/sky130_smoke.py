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
    assert get_generator("nmos_rvt", pdk_dir), "Missing nmos_rvt generator mapping"
    assert get_generator("pmos_lvt", pdk_dir), "Missing pmos_lvt generator mapping"
    assert get_generator("nmos_hvt", pdk_dir), "Missing nmos_hvt generator mapping"
    assert get_generator("nfet", pdk_dir), "Missing nfet generator mapping"
    assert get_generator("pfet", pdk_dir), "Missing pfet generator mapping"

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

    with set_context(lib):
        subckt_rvt = SubCircuit(name="SMOKE_NMOS_RVT", pins=["D", "G", "S", "B"], generator={"name": "MOS"})
    with set_context(subckt_rvt.elements):
        subckt_rvt.elements.append(
            Instance(
                name="M1",
                model="NMOS_RVT",
                pins={"D": "D", "G": "G", "S": "S", "B": "B"},
                parameters={"W": "1.0e-6", "L": "0.15e-6", "NF": "2", "M": "1"},
            )
        )
    lib.append(subckt_rvt)

    spec = importlib.util.spec_from_file_location("sky130_gen_param", pdk_dir / "gen_param.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    primitives = {}
    assert mod.gen_param(subckt, primitives, pdk_dir), "gen_param returned False"
    assert primitives, "gen_param produced no primitives"
    primitives_rvt = {}
    assert mod.gen_param(subckt_rvt, primitives_rvt, pdk_dir), "gen_param returned False for NMOS_RVT"
    assert primitives_rvt, "gen_param produced no primitives for NMOS_RVT"
    rvt_entries = [
        entry for entry in primitives_rvt.values()
        if entry.get("abstract_template_name") == "SMOKE_NMOS_RVT"
    ]
    assert rvt_entries, "Missing NMOS_RVT primitive entries"
    assert all(entry.get("vt_type") == "RVT" for entry in rvt_entries), "Missing RVT vt_type for NMOS_RVT"

    print("sky130 smoke tests: ok")


if __name__ == "__main__":
    main()
