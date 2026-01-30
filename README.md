# Circom → LLZK Benchmarks

This repository is a benchmark set for converting Circom programs to LLZK.

The Circom fork that includes an LLZK backend is here:

```
https://github.com/project-llzk/circom
```

The list of benchmarks is recorded in `benchmark_metadata.json`. It is organized into two top-level categories:

- `applications/`: Circom applications and end-to-end circuits.
- `libs/`: Common and popular Circom libraries used by applications.

## Running LLZK Over Benchmarks

Use the evaluation script with the LLZK-enabled Circom binary:

```
python3 circom_to_llzk_eval.py --[concrete|no-concrete] --benchmark_dir . --timeout [seconds] --circom-bin [path-to-circom-bin]
```
