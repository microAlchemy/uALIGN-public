"""Case definitions.

A case is data, not code: it knows nothing about which tool will run it. Cases
live in ``qual/cases/<kind>/*.yaml`` and are loaded without importing the flow
under test, so the full matrix can be enumerated on a machine that has no
working ALIGN build.

Benchmark inputs are owned by this repo. Netlists are inlined or sit next to
their YAML file -- never referenced out of the ALIGN checkout -- because a
benchmark whose input can change underneath it when you sync upstream is not a
benchmark.

Two file forms are supported.

Explicit cases::

    kind: structure
    pdk: FinFET14nm_Mock_PDK
    tier: standard
    cases:
      - id: diff_pair_nmos
        subckt: DIFF_PAIR
        netlist: |
          .subckt DIFF_PAIR ...
          .ends DIFF_PAIR

Swept cases, for the primitive matrix::

    kind: primitive
    pdk: FinFET14nm_Mock_PDK
    tier: exhaustive
    template:
      id: "nmos_{label}_nf{nf}_m{m}"
      subckt: NMOS_UNIT
      netlist: |
        .subckt NMOS_UNIT d g s b
        m0 d g s b n w={w} nf={nf} m={m}
        .ends NMOS_UNIT
      expect:
        width_nm: "{w_nm}"
    sweep:
      device:
        - {w: 360e-9, w_nm: 360, label: w360n}
        - {w: 720e-9, w_nm: 720, label: w720n}
      nf: [1, 2, 4]
      m: [1, 2]

Each sweep axis is a list whose entries are either scalars (bound to the axis
name) or mappings (merged into the substitution namespace, which is how one
axis can carry a value, its label, and its expected geometry together).
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Iterator

import yaml

from .paths import CASES_DIR

KINDS = ("primitive", "structure", "circuit")
# Deliberately distinct from the marker names in ALIGN's own root pytest.ini
# (nightly, regression), so "run the nightly tests" is never ambiguous.
TIERS = ("quick", "standard", "exhaustive")

_FILE_KEYS = {"kind", "pdk", "tier", "timeout_s", "flow", "cases", "template", "sweep", "tags"}
_CASE_KEYS = {"id", "subckt", "netlist", "netlist_file", "constraints", "expect",
              "flow", "tier", "timeout_s", "pdk", "kind", "tags"}
_FLOW_KEYS = {"flat", "seed", "nvariants", "placer_sa_iterations", "router_mode",
              "skip_gds", "effort", "extra_args"}

DEFAULT_TIMEOUT_S = 1800


class CaseError(ValueError):
    """A case file is malformed."""


@dataclass(frozen=True)
class FlowOptions:
    """Knobs that change the search, and therefore change the result.

    These are recorded with every result: an area or runtime number is
    meaningless without them.
    """

    flat: bool = False
    # ALIGN treats seed 0 as "let the C++ placer pick its default" (see
    # align/pnr/placer.py), so the default here is deliberately nonzero.
    seed: int = 1
    nvariants: int = 1
    placer_sa_iterations: int | None = None
    router_mode: str = "top_down"
    skip_gds: bool = False
    effort: int = 0
    extra_args: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> FlowOptions:
        if not d:
            return cls()
        unknown = set(d) - _FLOW_KEYS
        if unknown:
            raise CaseError(f"unknown flow options: {sorted(unknown)}")
        if "extra_args" in d:
            d = {**d, "extra_args": tuple(d["extra_args"])}
        return cls(**d)


@dataclass(frozen=True)
class Case:
    id: str
    kind: str
    pdk: str
    subckt: str
    netlist: str
    constraints: dict[str, Any] = field(default_factory=dict)
    expect: dict[str, Any] = field(default_factory=dict)
    flow: FlowOptions = field(default_factory=FlowOptions)
    tier: str = "standard"
    timeout_s: int = DEFAULT_TIMEOUT_S
    tags: tuple[str, ...] = ()
    source: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise CaseError(f"{self.id}: kind must be one of {KINDS}, got {self.kind!r}")
        if self.tier not in TIERS:
            raise CaseError(f"{self.id}: tier must be one of {TIERS}, got {self.tier!r}")
        if not self.netlist.strip():
            raise CaseError(f"{self.id}: empty netlist")

    @property
    def uid(self) -> str:
        """Stable, filesystem-safe identity including the PDK.

        Derived from the case rather than from PYTEST_CURRENT_TEST so that
        artifact paths are diffable across runs and across parallel workers.
        """
        return slug(f"{self.id}__{self.pdk}")

    def with_flow(self, **overrides: Any) -> Case:
        return replace(self, flow=replace(self.flow, **overrides))


def slug(text: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^0-9A-Za-z]+", "_", str(text))).strip("_")


def _substitute(value: Any, variables: dict[str, Any]) -> Any:
    """Recursively apply ``str.format`` using the sweep point's variables."""
    if isinstance(value, str):
        try:
            return value.format(**variables)
        except KeyError as exc:
            raise CaseError(f"template references undefined sweep variable {exc}") from exc
    if isinstance(value, dict):
        return {k: _substitute(v, variables) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute(v, variables) for v in value]
    return value


def _expand_sweep(sweep: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Cross product of the named axes, yielding one variable namespace each."""
    if not sweep:
        yield {}
        return
    axes: list[tuple[str, list[Any]]] = []
    for name, values in sweep.items():
        if not isinstance(values, list) or not values:
            raise CaseError(f"sweep axis {name!r} must be a non-empty list")
        axes.append((name, values))

    names = [a[0] for a in axes]
    for point in itertools.product(*(a[1] for a in axes)):
        variables: dict[str, Any] = {}
        for name, entry in zip(names, point):
            if isinstance(entry, dict):
                variables.update(entry)
            else:
                variables[name] = entry
        yield variables


def _build_case(raw: dict[str, Any], defaults: dict[str, Any], source: Path) -> Case:
    unknown = set(raw) - _CASE_KEYS
    if unknown:
        raise CaseError(f"{source.name}: unknown case keys {sorted(unknown)}")

    merged = {**defaults, **raw}

    netlist = merged.get("netlist")
    netlist_file = merged.pop("netlist_file", None)
    if netlist and netlist_file:
        raise CaseError(f"{source.name}: set either netlist or netlist_file, not both")
    if netlist_file:
        path = (source.parent / netlist_file).resolve()
        if not path.is_file():
            raise CaseError(f"{source.name}: netlist_file not found: {path}")
        netlist = path.read_text()

    for required in ("id", "kind", "pdk", "subckt"):
        if not merged.get(required):
            raise CaseError(f"{source.name}: case is missing required key {required!r}")

    return Case(
        id=str(merged["id"]),
        kind=str(merged["kind"]),
        pdk=str(merged["pdk"]),
        subckt=str(merged["subckt"]).upper(),
        netlist=netlist or "",
        constraints=merged.get("constraints") or {},
        expect=merged.get("expect") or {},
        flow=FlowOptions.from_dict(merged.get("flow")),
        tier=str(merged.get("tier", "standard")),
        timeout_s=int(merged.get("timeout_s", DEFAULT_TIMEOUT_S)),
        tags=tuple(merged.get("tags") or ()),
        source=str(source),
    )


def load_case_file(path: Path) -> list[Case]:
    raw = yaml.safe_load(path.read_text()) or {}
    if not isinstance(raw, dict):
        raise CaseError(f"{path.name}: top level must be a mapping")

    unknown = set(raw) - _FILE_KEYS
    if unknown:
        raise CaseError(f"{path.name}: unknown top-level keys {sorted(unknown)}")

    defaults = {k: raw[k] for k in ("kind", "pdk", "tier", "timeout_s", "flow", "tags") if k in raw}

    if "cases" in raw and "template" in raw:
        raise CaseError(f"{path.name}: use either an explicit `cases` list or a `template` + `sweep`")

    if "template" in raw:
        template = raw["template"]
        if not isinstance(template, dict):
            raise CaseError(f"{path.name}: template must be a mapping")
        return [
            _build_case(_substitute(template, variables), defaults, path)
            for variables in _expand_sweep(raw.get("sweep") or {})
        ]

    entries = raw.get("cases") or []
    if not isinstance(entries, list):
        raise CaseError(f"{path.name}: `cases` must be a list")
    return [_build_case(entry, defaults, path) for entry in entries]


def load_cases(kind: str | None = None, root: Path = CASES_DIR) -> list[Case]:
    """Load every case, optionally restricted to one kind.

    Returns an empty list when no case files exist yet, so the suite collects
    cleanly before any cases are written.
    """
    if kind is not None and kind not in KINDS:
        raise CaseError(f"unknown kind {kind!r}; expected one of {KINDS}")

    search_root = root if kind is None else root / f"{kind}s"
    if not search_root.is_dir():
        return []

    cases: list[Case] = []
    for path in sorted(search_root.rglob("*.yaml")) + sorted(search_root.rglob("*.yml")):
        cases.extend(load_case_file(path))

    if kind is not None:
        cases = [c for c in cases if c.kind == kind]

    duplicates = {c.uid for c in cases if sum(1 for o in cases if o.uid == c.uid) > 1}
    if duplicates:
        raise CaseError(f"duplicate case ids: {sorted(duplicates)}")
    return cases


def filter_tier(cases: Iterable[Case], tier: str | None) -> list[Case]:
    """Tiers are cumulative: quick < standard < exhaustive."""
    if tier is None:
        return list(cases)
    if tier not in TIERS:
        raise CaseError(f"unknown tier {tier!r}; expected one of {TIERS}")
    allowed = set(TIERS[: TIERS.index(tier) + 1])
    return [c for c in cases if c.tier in allowed]
