#!/usr/bin/env python3
import os
import pathlib
import sys

from align.cell_fabric.pdk import Pdk


def main():
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    pdk_dir = repo_root.parent / "ALIGN-pdk-sky130" / "SKY130_PDK"
    assert pdk_dir.is_dir(), f"PDK dir not found: {pdk_dir}"

    sys.path.insert(0, str(pdk_dir.parent))
    from SKY130_PDK.cap import CapGenerator

    pdk = Pdk().load(pdk_dir / "layers.json")
    cap = CapGenerator(pdk)
    os.environ["ALIGN_SKY130_STRICT"] = "1"
    os.environ["ALIGN_SKY130_STRICT_CAPMIM_M4_ENC"] = "1"
    cap.addCap(1600, 1600)
    cap.computeBbox()
    cap.gen_data(run_drc=True, run_pex=False)

    assert cap.drc is not None, "Cap generator missing DRC output"
    assert len(cap.drc.errors) == 0, f"Cap generator DRC errors: {cap.drc.errors}"
    assert len(cap.rd.opens) == 0, f"Cap generator OPENs: {cap.rd.opens}"
    assert len(cap.rd.different_widths) == 0, f"Cap generator width mismatches: {cap.rd.different_widths}"

    print("sky130 cap smoke: ok")


if __name__ == "__main__":
    main()
