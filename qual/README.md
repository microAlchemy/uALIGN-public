# `qual/` — ALIGN Qualification & Profiling Suite

A pytest-based benchmark suite that measures what ALIGN can and cannot do
today, so we can decide which parts to use as-is, which to fix, and which to
replace.

## Output

- generation success/failure (`status`, `generated`);
- runtime (`wall_time_s`);
- DRC violations (`align_drc_errors`, signoff count when a deck is wired);
- LVS status (`lvs`);
- area (`area`, `w`, `h`);
- major routing issues (`routing_issues`, plus `open` / `short` buckets);
- determinism/repeatability (`deterministic`, plus findings on the result);
- amount of manual cleanup required (`cleanup`: none / minor / major / complete);
- analog figures of merit (`area_efficiency_pct`, `total_wirelength_um`, `total_vias`, `max_diff_net_mismatch_pct`, `max_diff_via_delta`, `analog_quality_score`);
- post-layout simulation metrics (`sim_metrics` when ngspice is available).


## Running it

```bash
cd $ALIGN_HOME
pip install -r qual/requirements.txt

pytest qual -m quick                      # fast subset, every commit
pytest qual -m "quick or standard" -n 8   # the nightly run, parallel
pytest qual -m exhaustive -n 16           # complete primitive sweeps, weekly

pytest qual -m primitive                  # one kind
pytest qual --kind circuit --pdk FinFET14nm_Mock_PDK
pytest qual --case diff_pair_nmos         # one case
pytest qual --repeats 1                   # skip extra same-seed runs
pytest qual --seeds 1,2,3,4               # seed-sensitivity characterization

pytest qual --collect-only               # review the matrix; needs no ALIGN build
pytest qual --update-baseline            # snapshot reality, then review the diff
```

There is one test file. Cases are selected, not split across scripts: `-m` for
tier and kind, `--kind` / `--pdk` / `--case` for the matrix. Determinism is a
check on each run (same seed, extra repeats), not a separate test.

Tiers are ordinary pytest markers (`quick`, `standard`, `exhaustive`) rather
than a second bespoke mechanism, so `-m` and `-k` work as usual. The names
avoid `nightly` and `regression` on purpose: those are ALIGN's own marker
names, and reusing them would make "run the nightly tests" ambiguous.

### Options

| Option | Default | Purpose |
| --- | --- | --- |
| `--align-home` | the enclosing checkout | qualify a different ALIGN checkout with the same cases |
| `--align-python` | `$ALIGN_PYTHON`, else current | interpreter that can `import align` |
| `--artifacts-dir` | `qual/artifacts` | where records and per-case output land |
| `--baseline` | `qual/baselines/known_issues.yaml` | expectations file |
| `--update-baseline` | off | rewrite expectations from this run instead of asserting |
| `--area-tolerance` | `0.15` | fractional area growth tolerated before failing |
| `--pdk` | all | restrict the matrix (repeatable) |
| `--kind` | all | restrict to `primitive`, `structure`, and/or `circuit` (repeatable) |
| `--case` | all | restrict to these case ids (repeatable) |
| `--repeats` | `2` | same-seed runs for the determinism check (`1` disables it) |
| `--seeds` | off | comma-separated seeds for seed-sensitivity characterization |
| `--discard-run-dir` | off | delete each case's working directory after recording |

### Purpose

The main purpose of this testing suite is to address the following problems and ensure quality in these aread:
- generated dimensions;
- PDK legality;
- expected device parameters;
- DRC;
- LVS.
