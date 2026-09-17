"""Analog figures of merit, symmetry, and parasitic balance verification.

DRC and LVS cleanliness is merely a minimum manufacturability gate. In analog
design, true layout quality is determined by physical layout effects:
- Routing symmetry on differential net pairs (ΔL, Δvia, ΔR, ΔC)
- Placement symmetry and common-centroid axis alignment
- Active area packing efficiency (active silicon vs. dead white-space)
- Routing parasitics and interconnect density

This check inspects the layout geometry and placement collateral directly from
ALIGN's 3_pnr output to produce quantifiable, deterministic analog figures of merit.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..case import Case
from ..result import Category, Finding, RunResult, Severity

SCALE = 10000.0  # ALIGN coordinate units per um (1e-4 um)

# Typical FinFET/bulk metal ballpark parameters for zero-simulator parasitic estimation
SHEET_RES_OHM_PER_SQ = 0.10
VIA_RES_OHM = 5.0
CAP_AREA_FF_PER_UM2 = 0.030
CAP_FRINGE_FF_PER_UM = 0.050

KNOWN_DIFF_PAIRS = (
    ("VIN", "VIP"), ("VIP", "VIN"),
    ("VON", "VOP"), ("VOP", "VON"),
    ("INN", "INP"), ("INP", "INN"),
    ("OUTN", "OUTP"), ("OUTP", "OUTN"),
    ("VINN", "VINP"), ("VINP", "VINN"),
    ("VOUTN", "VOUTP"), ("VOUTP", "VOUTN"),
    ("VIN_D", "VIP_D"), ("VIP_D", "VIN_D"),
    ("VIN_O", "VIP_O"), ("VIP_O", "VIN_O"),
    ("VIN1", "VIN2"), ("VIN2", "VIN1"),
    ("VOUT_CTLE1", "VOUT_CTLE2"), ("VOUT_CTLE2", "VOUT_CTLE1"),
    ("NET012", "NET06"), ("NET06", "NET012"),
    ("NET014", "NET8"), ("NET8", "NET014"),
    ("NET021", "NET022"), ("NET022", "NET021"),
    ("NET5", "NET8"), ("NET8", "NET5"),
    ("S0_CTLE", "S3_CTLE"), ("S3_CTLE", "S0_CTLE"),
)


def check(case: Case, result: RunResult, run_dir: Path | None = None) -> list[Finding]:
    """Extract analog layout figures of merit and verify symmetry."""
    if run_dir is None or not run_dir.is_dir():
        return []

    pnr_dir = run_dir / "3_pnr"
    if not pnr_dir.is_dir():
        return []

    layout_file = pnr_dir / f"{case.subckt}_0.json"
    if not layout_file.is_file():
        # Fallback to any non-gds json
        candidates = [
            f for f in pnr_dir.glob("*_0.json")
            if not f.name.endswith(".python.gds.json") and not f.name.endswith(".gds.json")
        ]
        if not candidates:
            return []
        layout_file = candidates[0]

    try:
        layout_data = json.loads(layout_file.read_text())
    except (json.JSONDecodeError, OSError):
        return []

    findings: list[Finding] = []

    # 1. Area Efficiency & Geometry Metrics
    bbox = layout_data.get("bbox", [0, 0, 0, 0])
    w_um = (bbox[2] - bbox[0]) / SCALE
    h_um = (bbox[3] - bbox[1]) / SCALE
    bbox_area_um2 = max(0.0, w_um * h_um)

    active_area_um2 = 0.0
    net_wirelength_um: dict[str, float] = {}
    net_vias: dict[str, int] = {}
    net_rects: dict[str, list[list[float]]] = {}

    for term in layout_data.get("terminals", []):
        layer = term.get("layer", "")
        net = term.get("netName")
        rect = term.get("rect", [0, 0, 0, 0])
        rw_um = max(0.0, (rect[2] - rect[0]) / SCALE)
        rh_um = max(0.0, (rect[3] - rect[1]) / SCALE)

        if layer == "Active":
            active_area_um2 += rw_um * rh_um

        if net:
            net_upper = str(net).upper()
            if layer.startswith("M") and len(layer) > 1 and layer[1:].isdigit():
                seg_len = max(rw_um, rh_um)
                net_wirelength_um[net_upper] = net_wirelength_um.get(net_upper, 0.0) + seg_len
                net_rects.setdefault(net_upper, []).append([rw_um, rh_um])
            elif layer.startswith("V") and len(layer) > 1 and layer[1:].isdigit():
                net_vias[net_upper] = net_vias.get(net_upper, 0) + 1

    area_efficiency_pct = (
        (active_area_um2 / bbox_area_um2 * 100.0) if bbox_area_um2 > 0 else 0.0
    )
    total_wirelength_um = sum(net_wirelength_um.values())
    total_vias = sum(net_vias.values())

    result.active_area_um2 = round(active_area_um2, 6)
    result.area_efficiency_pct = round(area_efficiency_pct, 2)
    result.total_wirelength_um = round(total_wirelength_um, 4)
    result.total_vias = total_vias

    findings.append(Finding(
        category=Category.ANALOG,
        code="area_efficiency",
        severity=Severity.INFO,
        message=f"area efficiency: {area_efficiency_pct:.2f}% "
                f"(active {active_area_um2:.4f} um² / bbox {bbox_area_um2:.4f} um²)",
        data={"active_area_um2": round(active_area_um2, 6),
              "bbox_area_um2": round(bbox_area_um2, 6),
              "efficiency_pct": round(area_efficiency_pct, 2)},
    ))

    # 2. Differential Net Symmetry Analysis
    diff_pairs = _find_differential_pairs(case, pnr_dir, net_wirelength_um)
    max_mismatch_pct = 0.0
    max_via_delta = 0
    symmetry_penalties = 0.0

    for net_a, net_b in diff_pairs:
        l_a = net_wirelength_um.get(net_a, 0.0)
        l_b = net_wirelength_um.get(net_b, 0.0)
        v_a = net_vias.get(net_a, 0)
        v_b = net_vias.get(net_b, 0)

        delta_l = abs(l_a - l_b)
        max_l = max(l_a, l_b, 0.001)
        mismatch_pct = (delta_l / max_l) * 100.0
        delta_v = abs(v_a - v_b)

        r_a, c_a = _estimate_rc(net_rects.get(net_a, []), v_a)
        r_b, c_b = _estimate_rc(net_rects.get(net_b, []), v_b)
        delta_r = abs(r_a - r_b)
        delta_c = abs(c_a - c_b)

        if mismatch_pct > max_mismatch_pct:
            max_mismatch_pct = mismatch_pct
        if delta_v > max_via_delta:
            max_via_delta = delta_v

        # Score penalties for asymmetry
        if mismatch_pct > 5.0:
            symmetry_penalties += min(25.0, (mismatch_pct - 5.0) * 0.5)
        if delta_v > 2:
            symmetry_penalties += min(20.0, (delta_v - 2) * 2.0)

        data = {
            "net1": net_a, "net2": net_b,
            "length1_um": round(l_a, 3), "length2_um": round(l_b, 3),
            "delta_length_um": round(delta_l, 4), "mismatch_pct": round(mismatch_pct, 2),
            "vias1": v_a, "vias2": v_b, "delta_vias": delta_v,
            "delta_r_est_ohm": round(delta_r, 2), "delta_c_est_ff": round(delta_c, 2),
        }

        if mismatch_pct > 25.0 or delta_v > 5:
            findings.append(Finding(
                category=Category.ANALOG,
                code="diff_net_asymmetry",
                severity=Severity.WARNING,
                message=f"differential pair {net_a}/{net_b} asymmetric: "
                        f"ΔL={delta_l:.3f}um ({mismatch_pct:.1f}%), Δvia={delta_v}, "
                        f"ΔR_est={delta_r:.1f}Ω, ΔC_est={delta_c:.2f}fF",
                data=data,
            ))
        else:
            findings.append(Finding(
                category=Category.ANALOG,
                code="diff_net_symmetry_ok",
                severity=Severity.INFO,
                message=f"differential pair {net_a}/{net_b} balanced: "
                        f"ΔL={delta_l:.3f}um ({mismatch_pct:.1f}%), Δvia={delta_v}",
                data=data,
            ))

    if diff_pairs:
        result.max_diff_net_mismatch_pct = round(max_mismatch_pct, 2)
        result.max_diff_via_delta = max_via_delta

    # 3. Placement Symmetry Verification
    placement_findings, placement_penalty = _verify_placement_symmetry(case, pnr_dir)
    findings.extend(placement_findings)
    symmetry_penalties += placement_penalty

    # 4. Analog Quality Score (0 - 100)
    score = 100.0 - symmetry_penalties
    if area_efficiency_pct < 4.0 and case.kind == "circuit":
        score -= 5.0
    result.analog_quality_score = max(0.0, min(100.0, round(score, 1)))

    findings.append(Finding(
        category=Category.ANALOG,
        code="analog_quality_score",
        severity=Severity.INFO,
        message=f"analog layout quality score: {result.analog_quality_score}/100",
        data={"score": result.analog_quality_score,
              "max_diff_net_mismatch_pct": result.max_diff_net_mismatch_pct,
              "max_diff_via_delta": result.max_diff_via_delta,
              "area_efficiency_pct": result.area_efficiency_pct},
    ))

    return findings


def _find_differential_pairs(
    case: Case,
    pnr_dir: Path,
    net_wirelength_um: dict[str, float],
) -> list[tuple[str, str]]:
    """Discover differential net pairs from constraints and naming heuristics."""
    pairs: list[tuple[str, str]] = []
    seen: set[frozenset[str]] = set()

    def _add_pair(n1: str, n2: str) -> None:
        u1, u2 = n1.upper(), n2.upper()
        if u1 == u2 or frozenset({u1, u2}) in seen:
            return
        if u1 in net_wirelength_um and u2 in net_wirelength_um:
            seen.add(frozenset({u1, u2}))
            pairs.append((u1, u2))

    # A. Check case constraints
    if isinstance(case.constraints, dict):
        for _, c_list in case.constraints.items():
            if isinstance(c_list, list):
                for c in c_list:
                    if isinstance(c, dict) and c.get("constraint") == "SymmetricNets":
                        n1 = c.get("net1")
                        n2 = c.get("net2")
                        if isinstance(n1, str) and isinstance(n2, str):
                            _add_pair(n1, n2)

    # B. Check ALIGN's pnr.const.json
    pnr_const_file = pnr_dir / "inputs" / f"{case.subckt}.pnr.const.json"
    if pnr_const_file.is_file():
        try:
            pnr_c = json.loads(pnr_const_file.read_text())
            for c in pnr_c.get("constraints", []):
                if isinstance(c, dict) and c.get("const_name") == "SymmNet":
                    n1 = c.get("net1", {}).get("name")
                    n2 = c.get("net2", {}).get("name")
                    if n1 and n2:
                        _add_pair(str(n1), str(n2))
        except (json.JSONDecodeError, OSError):
            pass

    # C. Check Known Pair List
    for n1, n2 in KNOWN_DIFF_PAIRS:
        _add_pair(n1, n2)

    # D. Suffix heuristics: _P/_N, P/N
    nets = list(net_wirelength_um.keys())
    for i, n1 in enumerate(nets):
        for n2 in nets[i + 1:]:
            if n1.endswith("_P") and n2.endswith("_N") and n1[:-2] == n2[:-2]:
                _add_pair(n1, n2)
            elif n1.endswith("_N") and n2.endswith("_P") and n1[:-2] == n2[:-2]:
                _add_pair(n1, n2)
            elif n1.endswith("P") and n2.endswith("N") and n1[:-1] == n2[:-1]:
                _add_pair(n1, n2)
            elif n1.endswith("N") and n2.endswith("P") and n1[:-1] == n2[:-1]:
                _add_pair(n1, n2)

    return pairs


def _estimate_rc(rects: list[list[float]], via_count: int) -> tuple[float, float]:
    """Rough parasitic R (ohms) and C (fF) estimate for a net."""
    total_r = via_count * VIA_RES_OHM
    total_c = 0.0
    for w, h in rects:
        area = w * h
        perimeter = 2.0 * (w + h)
        aspect = max(w, h) / max(0.01, min(w, h))
        total_r += aspect * SHEET_RES_OHM_PER_SQ
        total_c += area * CAP_AREA_FF_PER_UM2 + perimeter * CAP_FRINGE_FF_PER_UM
    return total_r, total_c


def _verify_placement_symmetry(case: Case, pnr_dir: Path) -> tuple[list[Finding], float]:
    """Verify that blocks declared symmetric share a common symmetry axis."""
    findings: list[Finding] = []
    penalty = 0.0

    placement_file = pnr_dir / "inputs" / f"{case.subckt}.scaled_placement_verilog.json"
    if not placement_file.is_file():
        return findings, penalty

    try:
        pdata = json.loads(placement_file.read_text())
    except (json.JSONDecodeError, OSError):
        return findings, penalty

    modules = pdata.get("modules", [])
    if not modules:
        return findings, penalty

    mod = modules[0]
    instances = {inst["instance_name"]: inst for inst in mod.get("instances", []) if "instance_name" in inst}
    leaves = {leaf.get("template_name"): leaf for leaf in pdata.get("leaves", []) if leaf.get("template_name")}

    # Read template files in inputs/ if leaf bbox is missing
    inputs_dir = pnr_dir / "inputs"

    def _get_bbox(inst: dict[str, Any]) -> list[float] | None:
        tname = inst.get("concrete_template_name") or inst.get("abstract_template_name")
        if not tname:
            return None
        if tname in leaves and leaves[tname].get("bbox"):
            return leaves[tname]["bbox"]
        tfile = inputs_dir / f"{tname}.json"
        if tfile.is_file():
            try:
                tdata = json.loads(tfile.read_text())
                return tdata.get("bbox")
            except (json.JSONDecodeError, OSError):
                pass
        return None

    # Check SymmetricBlocks constraints
    for c in mod.get("constraints", []):
        if c.get("constraint") != "SymmetricBlocks":
            continue
        direction = c.get("direction", "V")
        pairs = c.get("pairs", [])
        axes = []

        for pair in pairs:
            if not isinstance(pair, list):
                continue
            if len(pair) == 1:
                # Self-symmetric block
                bname = pair[0]
                inst = instances.get(bname)
                if not inst:
                    continue
                bbox = _get_bbox(inst)
                if not bbox:
                    continue
                ox = inst.get("transformation", {}).get("oX", 0)
                bw = (bbox[2] - bbox[0])
                center_x = ox + bw / 2.0
                axes.append(center_x)
            elif len(pair) == 2:
                # Pair of symmetric blocks
                b1, b2 = pair[0], pair[1]
                i1, i2 = instances.get(b1), instances.get(b2)
                if not i1 or not i2:
                    continue
                bb1, bb2 = _get_bbox(i1), _get_bbox(i2)
                if not bb1 or not bb2:
                    continue
                ox1 = i1.get("transformation", {}).get("oX", 0)
                ox2 = i2.get("transformation", {}).get("oX", 0)
                cx1 = ox1 + (bb1[2] - bb1[0]) / 2.0
                cx2 = ox2 + (bb2[2] - bb2[0]) / 2.0
                center = (cx1 + cx2) / 2.0
                axes.append(center)

        if len(axes) >= 2:
            spread = max(axes) - min(axes)
            spread_um = spread / SCALE
            if spread_um > 0.05:  # > 50 nm deviation
                penalty += 10.0
                findings.append(Finding(
                    category=Category.ANALOG,
                    code="placement_symmetry_mismatch",
                    severity=Severity.WARNING,
                    message=f"symmetry axis deviation across blocks: {spread_um:.3f} um (direction {direction})",
                    data={"axes": axes, "spread_um": spread_um, "direction": direction},
                ))
            else:
                findings.append(Finding(
                    category=Category.ANALOG,
                    code="placement_symmetry_ok",
                    severity=Severity.INFO,
                    message=f"placement symmetry verified: blocks share symmetry axis within {spread_um * 1000:.1f} nm",
                    data={"axes": axes, "spread_um": spread_um, "direction": direction},
                ))

    return findings, penalty
