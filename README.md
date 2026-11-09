# Circom → LLZK Benchmarks

This repository is a benchmark set for converting Circom programs to LLZK.

The Circom fork that includes an LLZK backend is here:

```
https://github.com/project-llzk/circom
```

Benchmark lists are stored in this repository's `metadata/` directory:

- `self.json`: this repository's benchmarks.
- `costa_tests.json`: the external Costa test suite.
- `circomlib.json`: the external Circomlib checkout.

This repository's circuits are organized into two top-level categories:

- `applications/`: Circom applications and end-to-end circuits.
- `libs/`: Common and popular Circom libraries used by applications.

Both scripts operate on an existing local source directory. Clone repositories,
check out the revisions you want to measure, and prepare any dependencies before
running them. Git is optional for local directories; neither script downloads
sources or installs packages.

## Generating metadata

Generate or refresh the metadata with:

```
python3 scripts/benchmark_detector.py
```

Both scripts accept `--benchmark_dir PATH` (also spelled `--benchmark-dir`) and
default to this repository's root, regardless of the caller's working directory.
Metadata defaults to this repository's `metadata/self.json` for its own sources,
or `metadata/<source-directory-name>.json` for external sources. Use
`--metadata-file PATH` to select another filename or location. The examples below
use this override to name the `costa_circom_tests` checkout's manifest
`metadata/costa_tests.json`.

| Path | Resolution |
| --- | --- |
| `--benchmark_dir` | Relative to the caller's working directory; defaults to this repository |
| `--metadata-file` | Explicit paths are relative to the caller's working directory; defaults to this repository's `metadata/self.json` or `metadata/<source-directory-name>.json` |
| Metadata entry `path` | Relative to `--benchmark_dir`, even when metadata is stored elsewhere |
| Detector `--scan-dir` and `--exclude` | Relative to `--benchmark_dir` |
| Evaluator `-l` / `--include-dir` | Relative to `--benchmark_dir`; absolute directories are also accepted |
| Evaluator `--output-dir` | Relative to the caller's working directory; defaults to `<benchmark_dir>/llzk-outputs` |

The detector finds `.circom` files with an uncommented `component main`
declaration, including declarations split across lines. Reusable templates
without a main component need an explicit wrapper that supplies their parameters.
Use repeatable `--scan-dir` options to scan particular subdirectories or files and
repeatable `--exclude` options to omit paths. Generated output, dependency, Git,
and cache directories are skipped by default; see `--help` for the full list.
Exclusions use `fnmatch` patterns against root-relative POSIX paths, with `*`
matching `/` as well. A matching directory excludes its descendants. For example,
`--scan-dir test/circuits` selects Circomlib's test wrappers, while
`--exclude 'tags_2.2/fails'` excludes that Costa subtree. Regeneration replaces the
manifest with the discovered selection, so excluded or removed entries disappear.

Generated entries default to `"enabled": true`. Set `enabled` to `false` to disable
a benchmark. Regeneration preserves settings by relative benchmark path and adds
`"enabled": true` to legacy entries missing the field. The evaluator runs only
entries explicitly set to `true`; it does not search the source directory for
additional benchmarks. Supplied `enabled` values must be JSON booleans. Missing,
malformed, or duplicate metadata paths are reported before running benchmarks.

When Git is available, generated `repository` and `commit` fields identify the
source checkout. For vendored circuits, these describe the containing checkout,
not necessarily the original upstream project.

## Running LLZK Over Benchmarks

Use the evaluation script with the LLZK-enabled Circom binary:

```
python3 scripts/circom_to_llzk_eval.py \
  --circom-bin /path/to/circom/target/debug/circom \
  --timeout 10 --nthreads 4 --no-concrete
```

Concrete mode is the default; use `--no-concrete` for templated mode. The compiler
runs with the source root as its working directory. Add `-l DIR` repeatedly when
extra include directories are required; there is no assumed `tests/libs` layout.
Compilation errors and timeouts are recorded per benchmark so the remaining
benchmarks can continue. The CSV's `Error Message` field retains up to 4,000
characters of compiler diagnostics (stderr, or stdout if stderr is empty).

Artifacts, the timing CSV, and `run_info.json` are stored under the output
directory in separate `concrete/` and `templated/` directories. Circuit artifact
directories preserve source-relative paths to avoid basename collisions.
For example, `test/circuits/sign_test.circom` writes under
`<output-dir>/concrete/circuits/test/circuits/sign_test.circom/`.
Run information records the selected metadata, compiler identity and options,
and the checkout revision and dirty state. Use a separate output directory for
each repository or revision; rerunning the same mode in the same output directory
replaces its results and refreshes artifacts previously managed by this evaluator.
An existing nonempty artifact directory without recognized run information is
left untouched; choose a fresh output directory in that case. Keep source files,
metadata, compiler binaries, and include directories outside the generated mode
directory to avoid output collisions.

## External checkouts

For example, from this repository:

```bash
python3 scripts/benchmark_detector.py \
  --benchmark_dir ../circomlib

# Review enabled flags in metadata/circomlib.json before running.
python3 scripts/circom_to_llzk_eval.py \
  --benchmark_dir ../circomlib \
  --output-dir benchmark_reports/circomlib \
  --circom-bin ../circom/target/debug/circom \
  --timeout 10 --nthreads 4

python3 scripts/benchmark_detector.py \
  --benchmark_dir ../costa_circom_tests \
  --metadata-file metadata/costa_tests.json

# Review enabled flags in metadata/costa_tests.json before running.
python3 scripts/circom_to_llzk_eval.py \
  --benchmark_dir ../costa_circom_tests \
  --metadata-file metadata/costa_tests.json \
  --output-dir benchmark_reports/costa_circom_tests \
  --circom-bin ../circom/target/debug/circom \
  --timeout 10 --nthreads 4
```

Circomlib's `test/circuits` contains instantiated circuits that include library
templates. The Costa test suite also contains intentional failure fixtures and
syntax that may not be supported by a particular LLZK compiler build. Discovery
does not establish compiler compatibility: review enabled flags and disable
unwanted failure cases, or exclude their paths during detection. Witness inputs
and JavaScript test dependencies are not required for this compilation benchmark.

## Testing the scripts

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```
