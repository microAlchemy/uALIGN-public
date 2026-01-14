#!/usr/bin/env python3
import pathlib

from align.compiler.util import get_generator


def main():
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    pdk_dir = repo_root.parent / "ALIGN-pdk-sky130" / "SKY130_PDK"
    assert pdk_dir.is_dir(), f"PDK dir not found: {pdk_dir}"

    assert not get_generator("cap_lw", pdk_dir), "Unexpected generator for cap_lw"
    assert not get_generator("res_stage", pdk_dir), "Unexpected generator for res_stage"
    assert not get_generator("my_nfet_block", pdk_dir), "Unexpected generator for my_nfet_block"

    assert get_generator("cap", pdk_dir), "Missing generator for cap alias"
    assert get_generator("res", pdk_dir), "Missing generator for res alias"
    assert get_generator("sky130_fd_pr__cap_mim_m3_1", pdk_dir), "Missing generator for cap model"
    assert get_generator("sky130_fd_pr__res_generic_nd", pdk_dir), "Missing generator for res model"

    print("sky130 generator filters: ok")


if __name__ == "__main__":
    main()
