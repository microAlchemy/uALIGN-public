"""Fixtures and options for the qualification suite.

Usage::

    pytest qual -m quick                    # fast subset
    pytest qual -m primitive                # one kind
    pytest qual -m "quick or standard" -n 8  # the nightly run, parallel
    pytest qual --kind circuit --pdk FinFET14nm_Mock_PDK
    pytest qual --case diff_pair_nmos
    pytest qual --update-baseline            # snapshot reality, then review the diff

The ALIGN checkout is resolved from --align-home, then $ALIGN_HOME, then the
``uALIGN-public`` submodule.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from qual.harness import HARNESS_VERSION, checks, report
from qual.harness.baseline import DEFAULT_AREA_TOLERANCE, DEFAULT_BASELINE, Baseline
from qual.harness.case import KINDS, Case
from qual.harness.checks.determinism import DEFAULT_REPEATS
from qual.harness.parametrize import case_of
from qual.harness.paths import DEFAULT_ARTIFACTS_ROOT, AlignInstall, ToolchainError
from qual.harness.result import Category, RunResult, Severity
from qual.harness.runner import Runner


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("qualification")
    group.addoption("--align-home", default=None,
                    help="Path to the ALIGN checkout under test (default: $ALIGN_HOME or ./uALIGN-public)")
    group.addoption("--align-python", default=None,
                    help="Interpreter that can import align (default: $ALIGN_PYTHON or this one)")
    group.addoption("--artifacts-dir", default=str(DEFAULT_ARTIFACTS_ROOT),
                    help="Where per-case run output and the summary table are written")
    group.addoption("--baseline", default=str(DEFAULT_BASELINE),
                    help="Expectations file to compare against")
    group.addoption("--update-baseline", action="store_true", default=False,
                    help="Rewrite the expectations file from this run instead of asserting")
    group.addoption("--area-tolerance", type=float, default=DEFAULT_AREA_TOLERANCE,
                    help="Fractional area growth tolerated before failing (default: %(default)s)")
    group.addoption("--pdk", action="append", default=[], dest="pdks",
                    help="Restrict the matrix to these PDKs (repeatable)")
    group.addoption("--kind", action="append", default=[], dest="kinds",
                    help="Restrict the matrix to these kinds (repeatable): "
                         f"{', '.join(KINDS)}")
    group.addoption("--case", action="append", default=[], dest="case_ids",
                    help="Restrict the matrix to these case ids (repeatable)")
    group.addoption("--repeats", type=int, default=DEFAULT_REPEATS,
                    help="Same-seed runs for the determinism check "
                         "(1 disables it; default: %(default)s)")
    group.addoption("--seeds", default=None,
                    help="Comma-separated seeds for seed-sensitivity characterization "
                         "(off by default)")
    group.addoption("--discard-run-dir", action="store_true", default=False,
                    help="Delete each case's working directory after recording its result")


# -- session scoped state ----------------------------------------------------


@pytest.fixture(scope="session")
def artifacts_root(pytestconfig: pytest.Config) -> Path:
    root = Path(pytestconfig.getoption("artifacts_dir")).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture(scope="session")
def align(pytestconfig: pytest.Config) -> AlignInstall:
    try:
        install = AlignInstall.resolve(
            home=pytestconfig.getoption("align_home"),
            python=pytestconfig.getoption("align_python"),
        )
        install.check_importable()
    except ToolchainError as exc:
        pytest.skip(f"flow under test unavailable: {exc}")
    return install


@pytest.fixture(scope="session")
def runner(align: AlignInstall, pytestconfig: pytest.Config, artifacts_root: Path) -> Runner:
    return Runner(
        align=align,
        artifacts_root=artifacts_root,
        keep_run_dir=not pytestconfig.getoption("discard_run_dir"),
    )


@pytest.fixture(scope="session")
def baseline(pytestconfig: pytest.Config) -> Baseline:
    """Shared across workers for reading; only the controller writes it."""
    return Baseline.load(
        path=Path(pytestconfig.getoption("baseline")),
        area_tolerance=pytestconfig.getoption("area_tolerance"),
    )

@pytest.fixture
def qualify(runner, align, baseline, artifacts_root, record_property, pytestconfig):
    """Run a case, record everything about it, then compare to its expectation.

    Deliberately the only place a qualification test asserts. Measurement
    always completes and is always written out, even when the assertion fails,
    so a red run still produces a full results table.
    """

    def _qualify(case: Case) -> RunResult:
        result = runner.run(case)

        result.signoff_drc_errors, drc_findings = checks.drc.check(case, result, align)
        result.lvs, lvs_findings = checks.lvs.check(case, result, align)
        result.findings.extend(drc_findings)
        result.findings.extend(lvs_findings)
        result.findings.extend(
            checks.geometry.check(case, result, Path(result.artifacts_dir or ".") / "run")
        )
        result.findings.extend(
            checks.analog.check(case, result, Path(result.artifacts_dir or ".") / "run")
        )
        result.findings.extend(
            checks.simulation.check(case, result, align)
        )
        result.findings.extend(checks.determinism.check(
            runner, case, result, repeats=pytestconfig.getoption("repeats"),
        ))
        seeds = _parse_seeds(pytestconfig.getoption("seeds"))
        if seeds:
            sweep = checks.determinism.sweep_seeds(runner, case, seeds, primary=result)
            result.findings.extend(sweep.findings)

        result.write(Path(result.artifacts_dir) / "result.json")
        report.emit(result, artifacts_root)
        for key, value in result.metrics().items():
            record_property(key, value)
        for finding in result.findings:
            if finding.category in (Category.DETERMINISM, Category.ANALOG, Category.SIMULATION):
                record_property(finding.code, finding.message)

        if pytestconfig.getoption("update_baseline"):
            return result

        det_errors = [
            f for f in result.findings
            if f.category is Category.DETERMINISM and f.severity is Severity.ERROR
        ]
        violations = baseline.compare(case, result)
        if det_errors or violations:
            lines = [f.message for f in det_errors]
            if violations:
                lines.append(baseline.explain(case, result, violations))
            pytest.fail("\n".join(lines), pytrace=False)
        return result

    return _qualify


# -- collection and reporting ------------------------------------------------


def pytest_sessionstart(session: pytest.Session) -> None:
    """Archive the previous run's results so this session starts clean."""
    config = session.config
    if hasattr(config, "workerinput"):
        return
    root = Path(config.getoption("artifacts_dir")).resolve()
    root.mkdir(parents=True, exist_ok=True)
    report.rotate(root)


def pytest_configure(config: pytest.Config) -> None:
    repeats = config.getoption("repeats")
    if repeats < 1:
        raise pytest.UsageError("--repeats must be at least 1")
    kinds = set(config.getoption("kinds"))
    unknown = kinds - set(KINDS)
    if unknown:
        raise pytest.UsageError(
            f"unknown --kind values: {sorted(unknown)}; expected one of {KINDS}"
        )
    try:
        _parse_seeds(config.getoption("seeds"))
    except ValueError as exc:
        raise pytest.UsageError(f"--seeds: {exc}") from exc


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    selected_pdks = set(config.getoption("pdks"))
    selected_kinds = set(config.getoption("kinds"))
    selected_cases = set(config.getoption("case_ids"))
    if not selected_pdks and not selected_kinds and not selected_cases:
        return
    keep, drop = [], []
    for item in items:
        case = case_of(item)
        if case is None or _case_selected(case, selected_pdks, selected_kinds, selected_cases):
            keep.append(item)
        else:
            drop.append(item)
    if drop:
        config.hook.pytest_deselected(items=drop)
        items[:] = keep


def _case_selected(
    case: Case,
    pdks: set[str],
    kinds: set[str],
    case_ids: set[str],
) -> bool:
    if pdks and case.pdk not in pdks:
        return False
    if kinds and case.kind not in kinds:
        return False
    if case_ids and case.id not in case_ids and case.uid not in case_ids:
        return False
    return True


def _parse_seeds(value: str | None) -> list[int]:
    if not value:
        return []
    seeds = [int(part.strip()) for part in value.split(",") if part.strip()]
    if value.strip() and len(seeds) < 2:
        raise ValueError("need at least two seeds")
    return seeds


def pytest_runtest_setup(item: pytest.Item) -> None:
    for marker, module in (("requires_drc", checks.drc), ("requires_lvs", checks.lvs)):
        if item.get_closest_marker(marker) is None:
            continue
        if marker == "requires_drc" and checks.drc.available_tool() is None:
            pytest.skip(f"no signoff DRC tool on PATH ({', '.join(module.CANDIDATE_TOOLS)})")
        if marker == "requires_lvs" and checks.lvs.missing_tools():
            pytest.skip(f"missing LVS tools: {', '.join(checks.lvs.missing_tools())}")


def pytest_terminal_summary(terminalreporter, exitstatus, config: pytest.Config) -> None:
    """Roll the run up into summary.csv and one line on the console."""
    if hasattr(config, "workerinput"):
        return

    root = Path(config.getoption("artifacts_dir")).resolve()
    results = report.dedupe_latest(report.load_results(root))
    if not results:
        return

    summary = report.write_summary_csv(results, root / report.SUMMARY_FILENAME)
    terminalreporter.write_sep("-", f"qualification (harness {HARNESS_VERSION})")
    terminalreporter.write_line(report.console_summary(results))
    terminalreporter.write_line(f"summary: {summary}")

    if config.getoption("update_baseline"):
        path = Path(config.getoption("baseline"))
        current = Baseline.load(path=path, area_tolerance=config.getoption("area_tolerance"))
        current.record_all(results)
        current.save()
        terminalreporter.write_line(f"baseline updated ({len(results)} cases): {path}")
        terminalreporter.write_line("review the diff before committing it")
