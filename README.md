# Circom → LLZK Benchmarks

This repository is a benchmark set for converting Circom programs to LLZK.

The Circom fork that includes an LLZK backend is here:

```
https://github.com/project-llzk/circom
```

The list of benchmarks is recorded in `benchmark_metadata.json`. It is organized into two top-level categories:

- `applications/`: Circom applications and end-to-end circuits.
- `libs/`: Common and popular Circom libraries used by applications.

Generate or refresh the metadata from the repository root with:

```
python3 scripts/benchmark_detector.py
```

Generated entries default to `"enabled": true`. Set an entry's `enabled` field to
`false` in `benchmark_metadata.json` to disable that benchmark. Regenerating the
metadata preserves existing settings by benchmark path and adds `"enabled": true`
to entries that do not yet have the field. The evaluator runs only entries with
`"enabled": true` in the metadata under `--benchmark_dir`.

## Running LLZK Over Benchmarks

Use the evaluation script with the LLZK-enabled Circom binary:

```
python3 scripts/circom_to_llzk_eval.py --[concrete|no-concrete] --benchmark_dir . --timeout [seconds] --circom-bin [path-to-circom-bin]
```
