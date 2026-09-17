"""Turn case files into pytest parameters.

Cases are loaded at collection time without importing the flow under test, so
``pytest --collect-only`` enumerates the whole matrix on a machine with no
working ALIGN build. That matters once the primitive sweeps are hundreds of
points: the matrix stays reviewable in a diff.

A case's tier and kind become pytest markers, so selection is ordinary pytest
selection rather than a second bespoke mechanism::

    pytest qual -m quick
    pytest qual -m primitive
    pytest qual -m "quick or standard"
    pytest qual --kind circuit --pdk FinFET14nm_Mock_PDK
"""

from __future__ import annotations

import pytest

from .case import Case, load_cases


def case_params(kind: str | None = None) -> list:
    """Parameters for the suite, each marked with its tier and kind."""
    return [
        pytest.param(
            case,
            id=case.uid,
            marks=(getattr(pytest.mark, case.tier), getattr(pytest.mark, case.kind)),
        )
        for case in load_cases(kind)
    ]


def case_of(item: pytest.Item) -> Case | None:
    """The Case behind a collected test item, if it has one."""
    callspec = getattr(item, "callspec", None)
    if callspec is None:
        return None
    value = callspec.params.get("case")
    return value if isinstance(value, Case) else None
