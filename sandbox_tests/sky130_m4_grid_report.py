#!/usr/bin/env python3
import argparse
import json
from collections import Counter
from pathlib import Path


def _load_layer_data(pdk_path: Path, layer_name: str):
    layers_path = pdk_path / "layers.json"
    data = json.loads(layers_path.read_text())
    scale_factor = data.get("ScaleFactor", 1)
    abstraction = data.get("Abstraction", [])
    for layer in abstraction:
        if layer.get("Layer") == layer_name:
            pitch = layer.get("Pitch")
            offset = layer.get("Offset", 0)
            direction = layer.get("Direction", "").upper()
            if isinstance(pitch, list):
                pitch = pitch[0] if pitch else None
            if isinstance(offset, list):
                offset = offset[0] if offset else 0
            if pitch is None:
                raise ValueError(f"Missing Pitch for {layer_name} in {layers_path}")
            return scale_factor, int(pitch), int(offset), direction
    raise ValueError(f"Layer {layer_name} not found in {layers_path}")


def _centerline(rect, direction):
    if direction == "H":
        return (rect[1] + rect[3]) // 2
    if direction == "V":
        return (rect[0] + rect[2]) // 2
    raise ValueError(f"Unknown layer direction '{direction}'")


def main():
    parser = argparse.ArgumentParser(description="Report routing grid alignment for a given layer.")
    parser.add_argument("--json", required=True, help="PnR JSON file to analyze.")
    parser.add_argument("--pdk", default="ALIGN-pdk-sky130/SKY130_PDK", help="PDK directory containing layers.json.")
    parser.add_argument("--layer", default="M4", help="Layer name to analyze (default: M4).")
    parser.add_argument("--fail-on-offgrid", action="store_true", help="Exit non-zero if off-grid centers are found.")
    parser.add_argument("--max-samples", type=int, default=8, help="Max sample centers to report.")
    args = parser.parse_args()

    pdk_path = Path(args.pdk)
    json_path = Path(args.json)
    scale_factor, pitch, offset, direction = _load_layer_data(pdk_path, args.layer)

    # Viewer JSON coordinates are scaled back to PDK units in gen_viewer_json.
    pitch_units = pitch
    offset_units = offset

    data = json.loads(json_path.read_text())
    centers = []
    for term in data.get("terminals", []):
        if term.get("layer") != args.layer:
            continue
        rect = term.get("rect", [])
        if len(rect) != 4:
            continue
        centers.append(_centerline(rect, direction))

    if not centers:
        print(f"No {args.layer} terminals found in {json_path}")
        return 0

    mods = Counter(((c - offset_units) % pitch_units) for c in centers)
    offgrid = [c for c in centers if ((c - offset_units) % pitch_units) != 0]

    print(f"Layer {args.layer} direction={direction} pitch={pitch_units} offset={offset_units}")
    print(f"Terminals: {len(centers)}  Off-grid: {len(offgrid)}")
    print("Mod buckets (mod pitch -> count):")
    for mod, count in mods.most_common():
        print(f"  {mod:>6}: {count}")
    if offgrid:
        samples = offgrid[: args.max_samples]
        print(f"Sample off-grid centers (PDK units, max {args.max_samples}): {samples}")

    if args.fail_on_offgrid and offgrid:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
