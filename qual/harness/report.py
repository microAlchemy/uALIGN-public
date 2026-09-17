"""Result recording and roll-up.

The table is the deliverable, not the red/green dot. Every run appends one
line per case to ``artifacts/results.jsonl`` and, at session end, a
``artifacts/summary.csv`` whose columns are a superset of ALIGN's own
``regression_summaries/*.csv`` so old summaries stay comparable.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .result import DRC_BUCKETS, RunResult

RESULTS_FILENAME = "results.jsonl"
SUMMARY_FILENAME = "summary.csv"
HISTORY_DIRNAME = "runs"

SUMMARY_COLUMNS = (
    "case_id", "kind", "pdk", "status", "generated", "outcome",
    "align_drc_errors", "signoff_drc_errors", "lvs",
    *DRC_BUCKETS, "routing_issues",
    "w", "h", "area", "aspect",
    "area_efficiency_pct", "total_wirelength_um", "total_vias",
    "max_diff_net_mismatch_pct", "max_diff_via_delta", "analog_quality_score",
    "wall_time_s", "peak_rss_mb",
    "layout_hash", "deterministic",
    "cleanup", "cleanup_count",
    "seed", "align_revision", "align_dirty", "timestamp",
)


def emit(result: RunResult, artifacts_root: Path) -> None:
    """Append one result to the run-level log.

    A single short line written in append mode is atomic enough for the
    parallel pytest workers that share this file.
    """
    artifacts_root.mkdir(parents=True, exist_ok=True)
    line = json.dumps(result.to_dict(), separators=(",", ":"))
    with (artifacts_root / RESULTS_FILENAME).open("a") as fp:
        fp.write(line + "\n")


def rotate(artifacts_root: Path) -> Path | None:
    """Archive the previous run's log so a session starts from an empty one.

    Without this, summary.csv would accumulate results from every run ever
    performed. The archive doubles as the trend history used to compare runs.
    """
    path = artifacts_root / RESULTS_FILENAME
    if not path.is_file() or path.stat().st_size == 0:
        return None
    history = artifacts_root / HISTORY_DIRNAME
    history.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archived = history / f"{stamp}.jsonl"
    path.replace(archived)
    return archived


def dedupe_latest(results: Iterable[RunResult]) -> list[RunResult]:
    """Keep the last result per case, so repeat runs do not double-count."""
    latest: dict[str, RunResult] = {}
    for result in results:
        latest[result.case_uid] = result
    return list(latest.values())


def load_results(artifacts_root: Path) -> list[RunResult]:
    path = artifacts_root / RESULTS_FILENAME
    if not path.is_file():
        return []
    results = []
    for line in path.read_text().splitlines():
        if line.strip():
            results.append(RunResult.from_dict(json.loads(line)))
    return results


def write_summary_csv(results: Iterable[RunResult], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fp:
        writer: csv.DictWriter[str] = csv.DictWriter(fp, fieldnames=list(SUMMARY_COLUMNS), extrasaction="ignore")
        writer.writeheader()
        for result in sorted(results, key=lambda r: (r.kind, r.case_id, r.pdk)):
            writer.writerow(result.metrics())
    return path


def tally(results: Iterable[RunResult]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in results:
        counts[result.outcome.value] = counts.get(result.outcome.value, 0) + 1
    return counts


def console_summary(results: list[RunResult]) -> str:
    if not results:
        return "no cases ran"
    counts = tally(results)
    parts = [f"{count} {name}" for name, count in sorted(counts.items())]
    total_time = sum(r.wall_time_s for r in results)
    return f"{len(results)} cases: " + ", ".join(parts) + f"  ({total_time:.0f}s total)"
