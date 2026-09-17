"""Checks turn run artifacts into Findings.

Every check has the same contract: it takes a case and its result, and returns
a list of Findings. Checks never raise on a bad layout and never assert -- a
case with twelve violations is a measurement, and the assertion happens once,
later, against the baseline.

``drc``, ``lvs`` and ``geometry`` need external tools or a real run to pin down
artifact formats, and report a not_implemented warning until filled in.
``determinism`` re-runs the case at the same seed and attaches its findings to
the primary result.
"""

from . import analog, determinism, drc, geometry, lvs, simulation

__all__ = ["analog", "determinism", "drc", "geometry", "lvs", "simulation"]
