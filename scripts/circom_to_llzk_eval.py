#!/usr/bin/env python3
"""Run metadata-selected Circom benchmarks through the LLZK frontend."""

from __future__ import annotations

import argparse
import csv
import datetime
import hashlib
import json
import math
import multiprocessing
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import List, Optional, Tuple

from benchmark_common import (
    DEFAULT_ROOT,
    git_info,
    load_metadata,
    resolve_metadata_path,
    resolve_root,
    write_json,
)

ARTIFACT_LAYOUT_VERSION = 1
ERROR_MESSAGE_MAX_CHARS = 4000
TaskResult = Tuple[str, str, str, str, Optional[int]]


def _contains(parent: Path, child: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _same_file(left: Path, right: Path) -> bool:
    if left.resolve() == right.resolve():
        return True
    return left.exists() and right.exists() and left.samefile(right)


def _validate_output_layout(
    mode_dir: Path,
    root: Path,
    benchmarks: List[str],
    metadata_path: Path | None,
    compiler_path: str,
    include_dirs: list[Path],
    csv_path: Path,
    input_aliases: list[Path],
) -> Path:
    """Check all inputs and output ownership before replacing generated files."""
    artifacts_dir = mode_dir / "circuits"
    info_path = mode_dir / "run_info.json"
    for path in (mode_dir, artifacts_dir, info_path, csv_path):
        if path.is_symlink():
            raise ValueError(f"Managed output path must not be a symlink: {path}")
    for path in (mode_dir, artifacts_dir):
        if path.exists() and not path.is_dir():
            raise ValueError(f"Managed output directory is not a directory: {path}")
    for path in (info_path, csv_path):
        if path.exists() and not path.is_file():
            raise ValueError(f"Managed output file is not a file: {path}")
    if _contains(mode_dir, root):
        raise ValueError(f"Output mode directory must not contain the benchmark source root: {mode_dir}")

    protected = [Path(path) for path in benchmarks] + list(include_dirs) + input_aliases
    if metadata_path is not None:
        protected.append(metadata_path)
    # Even an empty selection must not delete an explicitly configured compiler.
    expanded_compiler = os.path.expanduser(compiler_path)
    resolved_compiler = expanded_compiler if os.path.dirname(expanded_compiler) else shutil.which(expanded_compiler)
    if resolved_compiler is not None:
        protected.append(Path(resolved_compiler).absolute())
    for path in protected:
        if _contains(artifacts_dir, path.absolute()) or _contains(artifacts_dir, path.resolve()):
            raise ValueError(f"Input path overlaps managed artifact output: {path}")
        if any(_same_file(path, output) for output in (info_path, csv_path)):
            raise ValueError(f"Input path overlaps a managed output file: {path}")

    if artifacts_dir.exists() and any(artifacts_dir.iterdir()):
        previous = None
        try:
            with info_path.open(encoding="utf-8") as handle:
                previous = json.load(handle)
        except (OSError, ValueError):
            pass
        marker = previous.get("artifact_layout_version") if isinstance(previous, dict) else None
        if type(marker) is not int or marker != ARTIFACT_LAYOUT_VERSION:
            raise ValueError(
                f"Cannot refresh nonempty artifact directory without recognized run_info.json: "
                f"{artifacts_dir}. Use a fresh --output-dir."
            )
    return artifacts_dir


def _selected_paths(root: Path, entries: list[dict]) -> List[str]:
    """Validate enabled sources without inspecting or discovering circuits."""
    paths = []
    for entry in entries:
        if entry.get("enabled") is not True:
            continue
        source = root / entry["path"]
        try:
            source.resolve().relative_to(root)
        except ValueError as error:
            raise ValueError(f"Benchmark path escapes the source root: {entry['path']}") from error
        if not source.is_file():
            raise ValueError(f"Enabled benchmark does not exist or is not a file: {source}")
        # Retain the metadata path as the benchmark identity, including symlink aliases.
        paths.append(str(source))
    return sorted(paths)


def load_benchmarks(benchmark_dir: str | Path, metadata_path: str | Path | None = None) -> List[str]:
    """Return validated paths explicitly enabled in the selected metadata file."""
    root = resolve_root(benchmark_dir)
    return _selected_paths(root, load_metadata(resolve_metadata_path(root, metadata_path)))


def resolve_include_dirs(root: Path, values: list[str]) -> list[Path]:
    paths = []
    for value in values:
        path = Path(value).expanduser()
        path = (path if path.is_absolute() else root / path).resolve()
        if not path.is_dir():
            raise ValueError(f"Include directory does not exist or is not a directory: {path}")
        if path not in paths:
            paths.append(path)
    return paths


def compiler_identity(binary: str) -> dict:
    """Resolve an executable before changing cwd and record its version and content hash."""
    expanded = os.path.expanduser(binary)
    if os.path.dirname(expanded):
        executable = Path(expanded).resolve()
    else:
        found = shutil.which(expanded)
        if found is None:
            raise ValueError(f"Circom executable not found on PATH: {binary}")
        executable = Path(found).resolve()
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise ValueError(f"Circom executable does not exist or is not executable: {executable}")
    try:
        version = subprocess.run(
            [str(executable), "--version"], capture_output=True, text=True, timeout=10,
        )
    except subprocess.TimeoutExpired as error:
        raise ValueError(f"Circom --version timed out: {executable}") from error
    if version.returncode:
        raise ValueError(f"Circom --version failed: {(version.stderr or version.stdout).strip()}")
    digest = hashlib.sha256()
    with executable.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return {
        "path": str(executable),
        "version": (version.stdout or version.stderr).strip(),
        "sha256": digest.hexdigest(),
    }


def _run_task_unpack(packed: Tuple) -> TaskResult:
    return run_task(*packed)


def run_task(benchmark_name: str, args: List[str], timeout: float, cwd: str | None = None) -> TaskResult:
    start = time.perf_counter()
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        elapsed = time.perf_counter() - start
        if proc.returncode == 0:
            return (benchmark_name, "success", f"{elapsed:.6f}", "", proc.returncode)
        error_message = (proc.stderr or proc.stdout).strip()[:ERROR_MESSAGE_MAX_CHARS] or f"exit code {proc.returncode}"
        return (benchmark_name, "error", f"{elapsed:.6f}", error_message, proc.returncode)
    except subprocess.TimeoutExpired:
        elapsed = time.perf_counter() - start
        return (benchmark_name, "timeout", f"{elapsed:.6f}", "timeout", None)
    except OSError as error:
        elapsed = time.perf_counter() - start
        return (benchmark_name, "error", f"{elapsed:.6f}", str(error)[:ERROR_MESSAGE_MAX_CHARS], None)


def expectation_matches(expected: str, actual: str, exit_code: int | None) -> bool:
    """A failure expectation requires compiler rejection, not a timeout or crash."""
    if expected == "success":
        return actual == "success" and exit_code == 0
    # Signals have negative return codes; Rust panics conventionally exit 101.
    # Neither is evidence that the compiler correctly rejected the circuit.
    return (
        expected == "failure"
        and actual == "error"
        and exit_code is not None
        and exit_code > 0
        and exit_code != 101
    )


def run_circom_benchmarks(
    benchmarks: List[str],
    benchmark_dir: str | Path,
    timeout: float,
    concrete: bool,
    circom_bin: str,
    nthreads: int,
    output_dir: str | Path | None = None,
    include_dirs: list[Path] | None = None,
    metadata_path: Path | None = None,
    metadata_entries: list[dict] | None = None,
    compiler_info: dict | None = None,
    input_aliases: list[Path] | None = None,
):
    """Run selected benchmarks and write artifacts, CSV results, and run provenance."""
    root = resolve_root(benchmark_dir)
    llzk_opt = "concrete" if concrete else "templated"
    destination = (Path(output_dir).expanduser() if output_dir is not None else root / "llzk_outputs").resolve()
    mode_dir = destination / llzk_opt
    include_dirs = include_dirs or []
    output_path = mode_dir / f"circom_benchmarks_results_{llzk_opt}.csv"
    protected_sources = benchmarks + [str(root / entry["path"]) for entry in (metadata_entries or [])]
    artifacts_dir = _validate_output_layout(
        mode_dir, root, protected_sources, metadata_path, circom_bin, include_dirs, output_path,
        input_aliases or [],
    )
    expected_by_path = {
        entry["path"]: entry.get("expected", "success")
        for entry in (metadata_entries or [])
    }
    run_info = {
        "artifact_layout_version": ARTIFACT_LAYOUT_VERSION,
        "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "benchmark_dir": str(root),
        "metadata_file": str(metadata_path) if metadata_path is not None else None,
        "source": git_info(root),
        "compiler": compiler_info,
        "options": {
            "mode": llzk_opt,
            "timeout": timeout,
            "nthreads": nthreads,
            "include_dirs": [str(path) for path in include_dirs],
            "output_dir": str(destination),
        },
        "benchmarks": sorted(
            (dict(entry, expected=entry.get("expected", "success"))
             for entry in (metadata_entries or []) if entry.get("enabled") is True),
            key=lambda entry: entry["path"],
        ),
        "commands": [],
    }
    mode_dir.mkdir(parents=True, exist_ok=True)
    if artifacts_dir.exists():
        shutil.rmtree(artifacts_dir)
    artifacts_dir.mkdir()
    output_path.unlink(missing_ok=True)
    benchmark_args = []
    for benchmark in benchmarks:
        source = Path(benchmark)
        benchmark_name = source.relative_to(root).as_posix()
        # Keep the full source filename as a directory to avoid both flattened-name
        # collisions and collisions between foo.circom and foo/bar.circom.
        bench_out_dir = artifacts_dir / benchmark_name
        bench_out_dir.mkdir(parents=True, exist_ok=True)
        args = [circom_bin, "--llzk_plaintext", f"--llzk={llzk_opt}"]
        for path in include_dirs:
            args.extend(["-l", str(path)])
        args.extend(["-o", str(bench_out_dir), str(source.resolve())])
        benchmark_args.append((benchmark_name, args, timeout, str(root)))
        run_info["commands"].append({"benchmark": benchmark_name, "args": args})
    write_json(mode_dir / "run_info.json", run_info)

    results = []
    if not benchmark_args:
        print("No enabled benchmarks.")
    elif nthreads == 1:
        for task in benchmark_args:
            print(f"Running {task[0]}")
            results.append(run_task(*task))
            print(f"Exit condition: {results[-1][1]}")
    else:
        total = len(benchmark_args)
        print(f"Launching {total} benchmarking tasks.")
        next_milestone = 10
        with multiprocessing.Pool(min(nthreads, total)) as pool:
            for i, result in enumerate(pool.imap_unordered(_run_task_unpack, benchmark_args), start=1):
                results.append(result)
                pct = i * 100 // total
                if pct >= next_milestone:
                    print(f"Progress: {i}/{total} ({pct}%) complete")
                    next_milestone = (pct // 10 + 1) * 10

    results.sort()
    counts = {cause: sum(result[1] == cause for result in results) for cause in ("success", "error", "timeout")}
    rows = []
    counts.update(matched=0, mismatched=0)
    for benchmark_name, actual, elapsed, message, exit_code in results:
        expected = expected_by_path.get(benchmark_name, "success")
        matches = expectation_matches(expected, actual, exit_code)
        expectation = "match" if matches else "mismatch"
        counts["matched" if matches else "mismatched"] += 1
        rows.append((benchmark_name, actual, elapsed, message, expected, expectation, exit_code))
        if not matches:
            status = f"exit code {exit_code}" if exit_code is not None else "no exit code"
            print(f"Mismatch: {benchmark_name}: expected {expected}, got {actual} ({status})")
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Benchmark", "Result", "Time Seconds", "Error Message", "Expected", "Expectation", "Exit Code"])
        writer.writerows(rows)
    run_info["completed_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    run_info["results_summary"] = counts
    write_json(mode_dir / "run_info.json", run_info)
    print(f"success: {counts['success']}, errored: {counts['error']}, timeout: {counts['timeout']}")
    print(f"matched: {counts['matched']}, mismatched: {counts['mismatched']}")
    print(f"Results: {output_path}")
    return str(output_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run enabled Circom benchmarks selected exclusively by metadata.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--benchmark_dir", "--benchmark-dir", default=str(DEFAULT_ROOT), help="Benchmark source root.")
    parser.add_argument("--metadata-file", help="Metadata input; defaults to this repository's metadata/self.json for its own sources, or metadata/<source-directory-name>.json for external sources. Explicit paths are relative to the current directory.")
    parser.add_argument("--output-dir", help="Output root; defaults to <benchmark_dir>/llzk_outputs. Explicit paths are relative to the current directory.")
    parser.add_argument("-l", "--include-dir", action="append", default=[], help="Additional compiler include directory, relative to the benchmark root. Repeatable.")
    parser.add_argument("--timeout", type=float, default=2, help="Positive per-benchmark timeout in seconds.")
    parser.add_argument("--concrete", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--circom-bin", default="circom", help="Path or PATH name of the LLZK-enabled Circom executable. Use the compiler directly: wrappers may alter exit statuses and cause incorrect expectation matches.")
    parser.add_argument("--nthreads", type=int, default=os.cpu_count() or 1, help="Number of parallel jobs.")
    args = parser.parse_args(argv)
    if args.nthreads < 1:
        parser.error("--nthreads must be positive")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be finite and positive")
    start = time.perf_counter()
    try:
        root = resolve_root(args.benchmark_dir)
        metadata_path = resolve_metadata_path(root, args.metadata_file)
        entries = load_metadata(metadata_path)
        files = _selected_paths(root, entries)
        includes = resolve_include_dirs(root, args.include_dir)
        compiler = compiler_identity(args.circom_bin) if files else None
        # Keep caller-visible aliases protected too: resolving a symlink must not
        # make it safe to delete that input's original path during output refresh.
        input_aliases = [
            Path(args.metadata_file).expanduser().absolute()
            if args.metadata_file is not None else metadata_path
        ]
        for value in args.include_dir:
            path = Path(value).expanduser()
            input_aliases.append(path.absolute() if path.is_absolute() else root / path)
        expanded_compiler = os.path.expanduser(args.circom_bin)
        compiler_alias = expanded_compiler if os.path.dirname(expanded_compiler) else shutil.which(expanded_compiler)
        if compiler_alias is not None:
            input_aliases.append(Path(compiler_alias).absolute())
        print(f"Benchmark directory: {root}")
        print(f"Metadata: {metadata_path}")
        output_path = run_circom_benchmarks(
            files, root, args.timeout, args.concrete,
            compiler["path"] if compiler is not None else args.circom_bin,
            args.nthreads, args.output_dir, includes, metadata_path, entries, compiler,
            input_aliases,
        )
        with (Path(output_path).parent / "run_info.json").open(encoding="utf-8") as handle:
            summary = json.load(handle)["results_summary"]
    except (OSError, ValueError) as error:
        parser.error(str(error))
    elapsed = datetime.timedelta(seconds=time.perf_counter() - start)
    print(f"Total benchmark execution time: {elapsed}")
    return 1 if summary["mismatched"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
