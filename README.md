# Circom → LLZK Benchmarks

Generate benchmark metadata and measure Circom-to-LLZK compilation using
[LLZK-enabled Circom](https://github.com/project-llzk/circom).

Requires Python 3.9+ and a built Circom executable. Clone source repositories at
the desired revisions and prepare their dependencies before benchmarking. Run
the examples below from this repository's root.

## Metadata

Generate or refresh metadata for this repository:

```bash
python3 scripts/benchmark_detector.py
```

Both scripts accept `--benchmark_dir PATH` (alias `--benchmark-dir`) and default
to this repository's source root. Metadata defaults to this repository's
`metadata/self.json`, or `metadata/<source-directory-name>.json` for external
sources. Override the filename with `--metadata-file PATH`.

The detector finds `.circom` files declaring `component main`. Use repeatable
`--scan-dir` options to select source subdirectories or files and `--exclude`
patterns to omit them. Exclusions use `fnmatch`, where `*` also matches `/`;
matching directories exclude their descendants. See `--help` for default
directory exclusions.

Each metadata entry has:

| Field | Meaning |
| --- | --- |
| `path` | Circuit path relative to the source root |
| `enabled` | Run only when `true`; discovery defaults to `true` |
| `expected` | `success` or `failure`; defaults to `success` |
| `benchmark name` | Display name |
| `repository`, `commit` | Containing Git checkout, when available |

Regeneration preserves settings by path and removes entries outside the discovered
selection. Review `enabled` and `expected` before running. An enabled entry with
`"expected": "failure"` checks that the compiler rejects the circuit. Evaluation
selects benchmarks exclusively from metadata.

Explicit source-root, metadata-file, and output-directory paths are relative to
the caller's working directory. Metadata entry paths, scan scopes, exclusion
patterns, and relative include directories are relative to the source root.

## Run benchmarks

```bash
python3 scripts/circom_to_llzk_eval.py \
  --circom-bin ../circom/target/debug/circom \
  --timeout 10 --nthreads 4
```

`--circom-bin` should point directly to the compiler executable. Wrappers may
alter exit statuses and cause crashes to be counted as expected failures.

Concrete mode is the default; add `--no-concrete` for templated mode. `--timeout`
sets the limit per circuit. Add `-l DIR` or `--include-dir DIR` repeatedly for
additional compiler include directories.

For external checkouts, generate metadata and run each suite:

```bash
python3 scripts/benchmark_detector.py --benchmark_dir ../circomlib

python3 scripts/circom_to_llzk_eval.py \
  --benchmark_dir ../circomlib \
  --circom-bin ../circom/target/debug/circom \
  --timeout 10 --nthreads 4

python3 scripts/benchmark_detector.py \
  --benchmark_dir ../costa_circom_tests \
  --metadata-file metadata/costa_tests.json

python3 scripts/circom_to_llzk_eval.py \
  --benchmark_dir ../costa_circom_tests \
  --metadata-file metadata/costa_tests.json \
  --circom-bin ../circom/target/debug/circom \
  --timeout 10 --nthreads 4
```

## Results

Results default to this repository's `benchmark_results/<suite>/<mode>/`,
regardless of the caller's working directory or where the benchmark sources live.
The suite is `self` for this repository's sources and the source directory's name
for external sources. An explicit `--metadata-file` does not change the suite name.
The mode is `concrete` by default, or `templated` with `--no-concrete`:

```text
benchmark_results/
├── self/
│   ├── concrete/
│   └── templated/
├── circomlib/
│   └── concrete/
│   └── templated/
└── costa_circom_tests/
    └── concrete/
    └── templated/
```

Each mode directory contains `circuits/` with the generated LLZK artifacts,
`circom_benchmarks_results_<mode>.csv` with timings and outcomes, and `run_info.json`
with compiler settings and source provenance. Only the requested suite and mode
are created.

`--output-dir PATH` overrides the suite directory: results go directly under
`PATH/<mode>/`, with relative paths resolved from the caller's working directory.
Rerunning a mode replaces its managed artifacts and results; use separate output
directories to retain multiple runs or distinguish external checkouts with the
same directory name. `benchmark_results/` is ignored by Git and excluded from
benchmark discovery.

The CSV records actual `Result`, `Expected`, `Expectation` (`match` or `mismatch`),
exit code, timing, and up to 4,000 characters of compiler diagnostics. An expected
failure matches a positive nonzero compiler exit other than Rust panic code 101.
Timeouts, launch errors, and signal terminations reported as negative return
codes are mismatches.
The evaluator continues after individual compilation failures and exits **0**
when all outcomes match, **1** for mismatches, or **2** for setup errors.
