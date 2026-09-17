"""Qualification cases.

One parametrized test over every case. Kind, PDK, tier and individual case
ids are selection, not separate test files::

    pytest qual -m quick                     # tier
    pytest qual -m primitive                 # kind
    pytest qual -m "quick and circuit"
    pytest qual --kind structure --pdk FinFET14nm_Mock_PDK
    pytest qual --case diff_pair_nmos
"""

from __future__ import annotations

import pytest

from qual.harness.parametrize import case_params

CASES = case_params()


@pytest.mark.skipif(not CASES, reason="no cases defined yet")
@pytest.mark.parametrize("case", CASES)
def test_case(case, qualify):
    qualify(case)
