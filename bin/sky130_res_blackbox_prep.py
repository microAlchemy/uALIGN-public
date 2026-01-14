#!/usr/bin/env python3
import argparse
import json
import math
import os
import sys
from pathlib import Path

import gdspy

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from align.compiler import generate_hierarchy  # noqa: E402
from align.main import extract_netlist_files  # noqa: E402


def _load_layers(layers_path, layer_name):
    data = json.loads(layers_path.read_text())
    for entry in data.get("Abstraction", []):
        if entry.get("Layer") == layer_name:
            return entry
    raise KeyError(f"Layer {layer_name} not found in {layers_path}")


def _label_layer_info(layer_entry):
    label_layer = layer_entry.get("GdsLayerNo")
    label_dtype = None
    if "LabelLayerNo" in layer_entry:
        for ll in layer_entry["LabelLayerNo"]:
            if isinstance(ll, (list, tuple)):
                if len(ll) == 2:
                    return ll[0], ll[1]
                if len(ll) == 1:
                    return ll[0], 0
            elif isinstance(ll, int):
                return ll, 0
    gds_datatype = layer_entry.get("GdsDatatype", {})
    if "Label" in gds_datatype:
        label_dtype = gds_datatype["Label"]
    if label_dtype is None:
        label_dtype = gds_datatype.get("Draw", 0)
    return label_layer, label_dtype


def _pin_layer_info(layer_entry):
    gds_layer = layer_entry.get("GdsLayerNo")
    gds_datatype = layer_entry.get("GdsDatatype", {})
    pin_dtype = gds_datatype.get("Pin", gds_datatype.get("Draw", 0))
    return gds_layer, pin_dtype


def _snap_up(value, pitch):
    return int(math.ceil(float(value) / float(pitch)) * pitch)


def _macro_geometry(pins, pitch_nm, width_nm, offset_nm):
    pin_w_nm = float(width_nm)
    pin_h_nm = float(width_nm)
    margin_nm = float(pitch_nm)
    x_centers = [offset_nm + (i + 1) * pitch_nm for i in range(len(pins))]
    max_center = max(x_centers)
    bbox_w_nm = max_center + pin_w_nm / 2 + margin_nm
    bbox_w_nm = max(bbox_w_nm, 4 * pitch_nm)
    bbox_w_nm = _snap_up(bbox_w_nm, pitch_nm)

    y_center = offset_nm + 2 * pitch_nm
    bbox_h_nm = y_center + pin_h_nm / 2 + margin_nm
    bbox_h_nm = max(bbox_h_nm, 4 * pitch_nm)
    bbox_h_nm = _snap_up(bbox_h_nm, pitch_nm)

    return x_centers, y_center, bbox_w_nm, bbox_h_nm, pin_w_nm, pin_h_nm


def _nm_to_um(val_nm):
    return float(val_nm) / 1000.0


def _emit_gds(name, pins, layer_entry, out_dir, bbox_layer=(999, 0)):
    gds_layer, pin_dtype = _pin_layer_info(layer_entry)
    label_layer, label_dtype = _label_layer_info(layer_entry)
    pitch_nm = float(layer_entry.get("Pitch", 430))
    width_nm = float(layer_entry.get("Width", 250))
    offset_nm = float(layer_entry.get("Offset", 0))

    x_centers, y_center, bbox_w_nm, bbox_h_nm, pin_w_nm, pin_h_nm = _macro_geometry(
        pins, pitch_nm, width_nm, offset_nm
    )

    lib = gdspy.GdsLibrary(unit=1e-6, precision=1e-9)
    cell = lib.new_cell(name)

    bbox_ll = (0.0, 0.0)
    bbox_ur = (_nm_to_um(bbox_w_nm), _nm_to_um(bbox_h_nm))
    cell.add(gdspy.Rectangle(bbox_ll, bbox_ur, layer=bbox_layer[0], datatype=bbox_layer[1]))

    y1 = _nm_to_um(y_center - pin_h_nm / 2)
    y2 = _nm_to_um(y_center + pin_h_nm / 2)
    for pin, x_center in zip(pins, x_centers):
        x1 = _nm_to_um(x_center - pin_w_nm / 2)
        x2 = _nm_to_um(x_center + pin_w_nm / 2)
        cell.add(gdspy.Rectangle((x1, y1), (x2, y2), layer=gds_layer, datatype=pin_dtype))
        cell.add(
            gdspy.Label(
                pin,
                (x_center / 1000.0, y_center / 1000.0),
                layer=label_layer,
                texttype=label_dtype,
            )
        )

    out_path = out_dir / f"{name}.gds"
    lib.write_gds(str(out_path))
    return out_path


def _load_blackbox_primitives(topology_json, include_all):
    data = json.loads(topology_json.read_text())
    entries = []
    for entry in data:
        constraints = entry.get("constraints", [])
        is_blackbox = any(
            c.get("constraint") == "Generator"
            and str(c.get("name", "")).lower() == "black_box"
            for c in constraints
        )
        if not is_blackbox:
            continue
        if not include_all:
            models = [e.get("model", "").upper() for e in entry.get("elements", [])]
            if not any("RES" in m for m in models):
                continue
        pins = entry.get("pins", [])
        if not pins:
            continue
        entries.append({"name": entry["name"], "pins": pins})
    return entries


def _ensure_topology(netlist_dir, pdk_dir, work_dir, netlist_file=None, subckt=None, overwrite=False):
    work_dir = work_dir.resolve()
    topology_dir = work_dir / "1_topology"
    topology_json = topology_dir / "__primitives_library__.json"
    if topology_json.exists() and not overwrite:
        return topology_dir

    netlist = extract_netlist_files(netlist_dir, netlist_file)
    design = subckt or netlist.stem.upper()
    topology_dir.mkdir(parents=True, exist_ok=True)
    generate_hierarchy(netlist, design, topology_dir, False, pdk_dir)
    if not topology_json.exists():
        raise FileNotFoundError(f"Missing primitives library: {topology_json}")
    return topology_dir


def main():
    ap = argparse.ArgumentParser(
        description="Generate placeholder black-box GDS for SKY130 resistor primitives."
    )
    ap.add_argument("netlist_dir", type=Path, help="directory containing a single .sp/.cdl netlist")
    ap.add_argument("-p", "--pdk", dest="pdk_dir", type=Path, required=True, help="PDK directory")
    ap.add_argument("--netlist-file", type=Path, default=None, help="explicit netlist file")
    ap.add_argument("--subckt", default=None, help="top subckt name (defaults to netlist filename)")
    ap.add_argument("--work-dir", type=Path, default=Path.cwd(), help="work dir for 1_topology")
    ap.add_argument("--topology-dir", type=Path, default=None, help="reuse existing 1_topology directory")
    ap.add_argument("-o", "--out-dir", type=Path, default=Path("blackbox"), help="output directory for GDS")
    ap.add_argument("--pin-layer", default="M1", help="routing layer to use for pins (default: M1)")
    ap.add_argument("--include-all", action="store_true", help="emit macros for any black_box primitive")
    ap.add_argument("--overwrite", action="store_true", help="overwrite existing GDS files")
    args = ap.parse_args()

    if os.getenv("ALIGN_SKY130_RES_BLACKBOX", "").lower() not in {"1", "true", "yes", "on"}:
        os.environ["ALIGN_SKY130_RES_BLACKBOX"] = "1"

    netlist_dir = args.netlist_dir.resolve()
    pdk_dir = args.pdk_dir.resolve()
    if not netlist_dir.is_dir():
        raise FileNotFoundError(f"Netlist dir not found: {netlist_dir}")
    if not pdk_dir.is_dir():
        raise FileNotFoundError(f"PDK dir not found: {pdk_dir}")

    if args.topology_dir:
        topology_dir = args.topology_dir.resolve()
        topology_json = topology_dir / "__primitives_library__.json"
        if not topology_json.exists():
            raise FileNotFoundError(f"Missing primitives library: {topology_json}")
    else:
        topology_dir = _ensure_topology(
            netlist_dir,
            pdk_dir,
            args.work_dir,
            netlist_file=args.netlist_file,
            subckt=args.subckt,
            overwrite=args.overwrite,
        )
        topology_json = topology_dir / "__primitives_library__.json"

    layers_json = pdk_dir / "layers.json"
    layer_entry = _load_layers(layers_json, args.pin_layer)

    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    entries = _load_blackbox_primitives(topology_json, include_all=args.include_all)
    if not entries:
        raise RuntimeError(f"No black_box primitives found in {topology_json}")

    generated = []
    for entry in entries:
        out_path = out_dir / f"{entry['name']}.gds"
        if out_path.exists() and not args.overwrite:
            generated.append(out_path)
            continue
        generated.append(_emit_gds(entry["name"], entry["pins"], layer_entry, out_dir))

    manifest = {
        "pdk_dir": str(pdk_dir),
        "netlist_dir": str(netlist_dir),
        "topology_dir": str(topology_dir),
        "macros": [{"name": e["name"], "pins": e["pins"]} for e in entries],
    }
    (out_dir / "blackbox_manifest.json").write_text(json.dumps(manifest, indent=2))

    for path in generated:
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
