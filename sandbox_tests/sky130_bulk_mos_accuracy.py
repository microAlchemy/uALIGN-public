#!/usr/bin/env python3
import pathlib
import sys

from align.cell_fabric.pdk import Pdk


def main():
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    pdk_dir = repo_root.parent / "ALIGN-pdk-sky130" / "SKY130_PDK"
    assert pdk_dir.is_dir(), f"PDK dir not found: {pdk_dir}"

    sys.path.insert(0, str(pdk_dir.parent))
    from SKY130_PDK.mos import MOSGenerator

    pdk = Pdk().load(pdk_dir / "layers.json")
    params = {
        "M1": {
            "W": "3.7e-7",
            "L": "1.6e-7",
            "NF": "2",
            "M": "1",
        }
    }

    mos = MOSGenerator(
        pdk,
        height=28,
        fin=5,
        gate=1,
        gateDummy=3,
        shared_diff=1,
        stack=1,
        bodyswitch=1,
        primitive_parameters=params,
    )

    active_width = mos.active.clg.grid[0][1][0]
    poly_width = mos.pl.clg.grid[0][1][0]
    assert active_width == 370, f"Expected active width 370nm, got {active_width}"
    assert poly_width == 160, f"Expected poly width 160nm, got {poly_width}"

    print("sky130 bulk mos accuracy: ok")


if __name__ == "__main__":
    main()
