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

Concrete mode is the default; add `--no-concrete` for templated mode. `--timeout`
sets the limit per circuit. Add `-l DIR` or `--include-dir DIR` repeatedly for
additional compiler include directories.

For external checkouts, generate metadata and run each suite:

```bash
python3 scripts/benchmark_detector.py --benchmark_dir ../circomlib

python3 scripts/circom_to_llzk_eval.py \
  --benchmark_dir ../circomlib \
  --output-dir benchmark_reports/circomlib \
  --circom-bin ../circom/target/debug/circom \
  --timeout 10 --nthreads 4

python3 scripts/benchmark_detector.py \
  --benchmark_dir ../costa_circom_tests \
  --metadata-file metadata/costa_tests.json

python3 scripts/circom_to_llzk_eval.py \
  --benchmark_dir ../costa_circom_tests \
  --metadata-file metadata/costa_tests.json \
  --output-dir benchmark_reports/costa_circom_tests \
  --circom-bin ../circom/target/debug/circom \
  --timeout 10 --nthreads 4
```

## Results

`--output-dir` defaults to `<source-root>/llzk_outputs`. Each mode has its own
directory containing circuit artifacts, a timing CSV, and `run_info.json` with
compiler settings and source provenance. Rerunning a mode replaces its managed
artifacts and results; use separate output directories to retain multiple runs.

The CSV records actual `Result`, `Expected`, `Expectation` (`match` or `mismatch`),
exit code, timing, and up to 4,000 characters of compiler diagnostics. An expected
failure matches a positive nonzero compiler exit other than Rust panic code 101.
Timeouts, launch errors, signal termination, and panics are always mismatches.
The evaluator continues after individual compilation failures and exits **0**
when all outcomes match, **1** for mismatches, or **2** for setup errors.
