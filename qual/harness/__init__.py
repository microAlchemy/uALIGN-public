"""Tool-agnostic harness for qualifying an analog place-and-route flow.

The four layers are kept separate on purpose:

``case``      declarative description of a benchmark case (data, no tool knowledge)
``runner``    adapter that turns a Case into a RunResult by invoking a flow
``checks``    interpret run artifacts into Findings (DRC, LVS, geometry, determinism)
``baseline``  compare a RunResult against its recorded expectation

Only ``runner`` and ``checks`` know which tool is under test, so replacing a
stage of ALIGN does not invalidate accumulated benchmark history.
"""

HARNESS_VERSION = "0.2.0"
