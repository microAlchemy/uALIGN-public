"""Device geometry and parameter verification.

This is the check that answers the sharpest Phase 1 question: does the flow
quantize or reinterpret planar MOS dimensions? The useful output is not a
boolean but the requested-versus-realized delta for every point in a sweep, so
the quantization rule itself becomes visible. Findings therefore always carry
both numbers in ``data``.

Expectations are declared per case::

    expect:
      device_count: 1
      width_nm: 360
      length_nm: 30
      nf: 2
      nfin: 4
      m: 1
      bbox_width_um: 0.384
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..case import Case
from ..result import Category, Finding, RunResult, Severity

# Fraction of the expected dimension we accept before calling it a mismatch.
DIMENSION_TOLERANCE = 0.001

_BBOX_EXPECTATIONS = {"bbox_width_um": "width_um", "bbox_height_um": "height_um"}
_DEVICE_EXPECTATIONS = {"device_count", "width_nm", "length_nm", "nf", "m", "nfin"}


def check(case: Case, result: RunResult, run_dir: Path | None = None) -> list[Finding]:
    if not case.expect:
        return []

    findings = list(_check_bbox(case, result))

    device_keys = _DEVICE_EXPECTATIONS & set(case.expect)
    if device_keys:
        findings.extend(_check_devices(case, run_dir))

    unknown = set(case.expect) - set(_BBOX_EXPECTATIONS) - _DEVICE_EXPECTATIONS
    if unknown:
        findings.append(Finding(
            category=Category.GEOMETRY, code="unknown_expectation", severity=Severity.WARNING,
            message=f"no checker knows how to verify {sorted(unknown)}",
        ))

    return findings


def _check_bbox(case: Case, result: RunResult):
    for key, attribute in _BBOX_EXPECTATIONS.items():
        if key not in case.expect:
            continue
        expected = float(case.expect[key])
        actual = getattr(result, attribute)
        if actual is None:
            yield Finding(
                category=Category.GEOMETRY, code="missing_geometry", severity=Severity.ERROR,
                message=f"{key} expected {expected} but no layout was produced",
            )
            continue
        delta = actual - expected
        if abs(delta) > expected * DIMENSION_TOLERANCE:
            yield Finding(
                category=Category.GEOMETRY, code="dimension_mismatch", severity=Severity.ERROR,
                message=f"{key}: requested {expected:.5f} um, realized {actual:.5f} um "
                        f"(delta {delta:+.5f} um, {delta / expected:+.2%})",
                data={"key": key, "expected_um": expected, "actual_um": actual,
                      "delta_um": delta, "relative": delta / expected},
            )


def load_primitives(run_dir: Path | None) -> dict[str, Any]:
    """Generated primitive descriptors, for device-level verification.

    Kept here so the parsing lives next to the check that will consume it.
    """
    if run_dir is None:
        return {}
    path = run_dir / "2_primitives" / "__primitives__.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    if isinstance(data, dict):
        return data
    if isinstance(data, list):
        return {str(i): item for i, item in enumerate(data)}
    return {}


def _extract_devices(primitives: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Extract (device_id, parameters_dict) pairs from loaded primitives."""
    devices: list[tuple[str, dict[str, Any]]] = []
    for prim_name, prim_data in primitives.items():
        if not isinstance(prim_data, dict):
            continue
        params = prim_data.get("parameters")
        if not isinstance(params, dict):
            continue
        for ele_name, ele_params in params.items():
            if isinstance(ele_params, dict) and any(
                k in ele_params for k in ("W", "L", "NFIN", "NF", "M", "R", "VALUE")
            ):
                devices.append((f"{prim_name}.{ele_name}", ele_params))
        # Fallback if parameters itself directly represents a device
        if not devices and any(k in params for k in ("W", "L", "NFIN", "NF", "M", "R", "VALUE")):
            devices.append((prim_name, params))
    return devices


def _check_devices(case: Case, run_dir: Path | None) -> list[Finding]:
    findings: list[Finding] = []
    primitives = load_primitives(run_dir)
    if not primitives:
        return [Finding(
            category=Category.GEOMETRY,
            code="missing_primitives",
            severity=Severity.ERROR,
            message="device expectations declared but 2_primitives/__primitives__.json was not generated",
            data={"expected": {k: case.expect[k] for k in _DEVICE_EXPECTATIONS & set(case.expect)}},
        )]

    devices = _extract_devices(primitives)
    if not devices:
        return [Finding(
            category=Category.GEOMETRY,
            code="no_devices_found",
            severity=Severity.ERROR,
            message="device expectations declared but no device parameters found in 2_primitives/__primitives__.json",
            data={"expected": {k: case.expect[k] for k in _DEVICE_EXPECTATIONS & set(case.expect)}},
        )]

    if "device_count" in case.expect:
        expected_count = int(case.expect["device_count"])
        actual_count = len(devices)
        if actual_count != expected_count:
            findings.append(Finding(
                category=Category.GEOMETRY,
                code="device_count_mismatch",
                severity=Severity.ERROR,
                message=f"device_count: expected {expected_count}, realized {actual_count}",
                data={"expected": expected_count, "actual": actual_count},
            ))

    if "width_nm" in case.expect:
        expected_w_nm = float(case.expect["width_nm"])
        for dev_id, dev_params in devices:
            if "W" in dev_params:
                try:
                    raw_w = float(dev_params["W"])
                    # ALIGN stores W in meters (e.g. 3.6e-7) or nm if > 1
                    actual_w_nm = raw_w * 1e9 if raw_w < 1e-3 else raw_w
                    delta = actual_w_nm - expected_w_nm
                    if abs(delta) > expected_w_nm * DIMENSION_TOLERANCE:
                        findings.append(Finding(
                            category=Category.GEOMETRY,
                            code="dimension_mismatch",
                            severity=Severity.ERROR,
                            message=f"{dev_id}.W: requested {expected_w_nm:.2f} nm, realized {actual_w_nm:.2f} nm "
                                    f"(delta {delta:+.2f} nm, {delta / expected_w_nm:+.2%})",
                            data={"device": dev_id, "key": "width_nm", "expected_nm": expected_w_nm,
                                  "actual_nm": actual_w_nm, "delta_nm": delta, "relative": delta / expected_w_nm},
                        ))
                except (ValueError, TypeError) as exc:
                    findings.append(Finding(
                        category=Category.GEOMETRY, code="unreadable_param", severity=Severity.ERROR,
                        message=f"{dev_id}.W unreadable value {dev_params['W']!r}: {exc}",
                    ))

    if "length_nm" in case.expect:
        expected_l_nm = float(case.expect["length_nm"])
        for dev_id, dev_params in devices:
            if "L" in dev_params:
                try:
                    raw_l = float(dev_params["L"])
                    actual_l_nm = raw_l * 1e9 if raw_l < 1e-3 else raw_l
                    delta = actual_l_nm - expected_l_nm
                    if abs(delta) > expected_l_nm * DIMENSION_TOLERANCE:
                        findings.append(Finding(
                            category=Category.GEOMETRY,
                            code="dimension_mismatch",
                            severity=Severity.ERROR,
                            message=f"{dev_id}.L: requested {expected_l_nm:.2f} nm, realized {actual_l_nm:.2f} nm "
                                    f"(delta {delta:+.2f} nm, {delta / expected_l_nm:+.2%})",
                            data={"device": dev_id, "key": "length_nm", "expected_nm": expected_l_nm,
                                  "actual_nm": actual_l_nm, "delta_nm": delta, "relative": delta / expected_l_nm},
                        ))
                except (ValueError, TypeError) as exc:
                    findings.append(Finding(
                        category=Category.GEOMETRY, code="unreadable_param", severity=Severity.ERROR,
                        message=f"{dev_id}.L unreadable value {dev_params['L']!r}: {exc}",
                    ))

    if "nf" in case.expect:
        expected_nf = int(case.expect["nf"])
        for dev_id, dev_params in devices:
            if "NF" in dev_params:
                try:
                    actual_nf = int(dev_params["NF"])
                    if actual_nf != expected_nf:
                        findings.append(Finding(
                            category=Category.GEOMETRY,
                            code="parameter_mismatch",
                            severity=Severity.ERROR,
                            message=f"{dev_id}.NF: requested {expected_nf}, realized {actual_nf}",
                            data={"device": dev_id, "key": "nf", "expected": expected_nf,
                                  "actual": actual_nf, "delta": actual_nf - expected_nf},
                        ))
                except (ValueError, TypeError) as exc:
                    findings.append(Finding(
                        category=Category.GEOMETRY, code="unreadable_param", severity=Severity.ERROR,
                        message=f"{dev_id}.NF unreadable value {dev_params['NF']!r}: {exc}",
                    ))

    if "m" in case.expect:
        expected_m = int(case.expect["m"])
        for dev_id, dev_params in devices:
            if "M" in dev_params:
                try:
                    actual_m = int(dev_params["M"])
                    if actual_m != expected_m:
                        findings.append(Finding(
                            category=Category.GEOMETRY,
                            code="parameter_mismatch",
                            severity=Severity.ERROR,
                            message=f"{dev_id}.M: requested {expected_m}, realized {actual_m}",
                            data={"device": dev_id, "key": "m", "expected": expected_m,
                                  "actual": actual_m, "delta": actual_m - expected_m},
                        ))
                except (ValueError, TypeError) as exc:
                    findings.append(Finding(
                        category=Category.GEOMETRY, code="unreadable_param", severity=Severity.ERROR,
                        message=f"{dev_id}.M unreadable value {dev_params['M']!r}: {exc}",
                    ))

    if "nfin" in case.expect:
        expected_nfin = int(case.expect["nfin"])
        for dev_id, dev_params in devices:
            if "NFIN" in dev_params:
                try:
                    actual_nfin = int(dev_params["NFIN"])
                    if actual_nfin != expected_nfin:
                        findings.append(Finding(
                            category=Category.GEOMETRY,
                            code="parameter_mismatch",
                            severity=Severity.ERROR,
                            message=f"{dev_id}.NFIN: requested {expected_nfin}, realized {actual_nfin}",
                            data={"device": dev_id, "key": "nfin", "expected": expected_nfin,
                                  "actual": actual_nfin, "delta": actual_nfin - expected_nfin},
                        ))
                except (ValueError, TypeError) as exc:
                    findings.append(Finding(
                        category=Category.GEOMETRY, code="unreadable_param", severity=Severity.ERROR,
                        message=f"{dev_id}.NFIN unreadable value {dev_params['NFIN']!r}: {exc}",
                    ))

    return findings
