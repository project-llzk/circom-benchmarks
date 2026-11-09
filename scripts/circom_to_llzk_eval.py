#!/usr/bin/env python3

# Program: circom_to_llzk_eval.py
# Description: This script runs the circom -> llzk frontend
#   on benchmarks listed in benchmark_metadata.json and writes a CSV with timing results.
#
# Required Programs:
#   - python3: For running this script
#   - circom: For compiling the benchmarks
#
# Usage:
#   scripts/circom_to_llzk_eval.py \
#       [--circom-bin PATH] \
#       [--benchmark_dir PATH] \
#       [--timeout SECONDS] \
#       [--nthreads N] \
#       [--no-concrete/--concrete]
#
# Example:
#   scripts/circom_to_llzk_eval.py --timeout 2 --circom-bin ~/gh/circom/target/release/circom --no-concrete

import argparse
import csv
import datetime
import json
import multiprocessing
import os
import subprocess
import time
from typing import List, Tuple

def load_benchmarks(benchmark_dir: str) -> List[str]:
    """Return sorted paths explicitly enabled in benchmark_metadata.json."""
    metadata_path = os.path.join(benchmark_dir, "benchmark_metadata.json")
    with open(metadata_path, "r", encoding="utf-8") as handle:
        entries = json.load(handle)
    return sorted(
        os.path.join(benchmark_dir, entry["path"])
        for entry in entries
        if entry.get("enabled") is True
    )

def _run_task_unpack(packed: Tuple) -> Tuple[str, str, str, str]:
    return run_task(*packed)

def run_task(benchmark_name: str, args: List[str], timeout: int) -> Tuple[str, str, str, str]:
    start = time.perf_counter()
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        elapsed = time.perf_counter() - start
        if proc.returncode == 0:
            return (benchmark_name, "success", f"{elapsed:.6f}", "")
        else:
            # currently taking only the first 400 characters from stderr
            # because the full dump can be a lot in some cases
            error_message = proc.stderr.strip()[:400]
            return (benchmark_name, "error", f"{elapsed:.6f}", error_message)
    except subprocess.TimeoutExpired:
        elapsed = time.perf_counter() - start
        return (benchmark_name, "timeout", f"{elapsed:.6f}", "timeout")

def run_circom_benchmarks(benchmarks: List[str], benchmark_dir: str, timeout: int, concrete: bool, circom_bin: str, nthreads: int):
    """Run circom->llzk on benchmarks and save timing/error results to a CSV."""
    results = []
    success_cnt = 0
    error_cnt = 0
    timeout_cnt = 0
    llzk_opt = "concrete" if concrete else "templated"

    benchmark_args = []
    for benchmark in benchmarks:
        benchmark_name = os.path.relpath(benchmark, benchmark_dir)
        bench_out_dir = os.path.join("llzk-outputs", benchmark_name.replace(os.sep, "_").removesuffix(".circom"))
        os.makedirs(bench_out_dir, exist_ok=True)
        args = [circom_bin, "--llzk_plaintext", f"--llzk={llzk_opt}", "-l", os.path.join(benchmark_dir, "tests/libs/"), "-o", bench_out_dir, benchmark]
        benchmark_args.append((benchmark_name, args, timeout))

    if nthreads == 1:
        for benchmark_name, args, _ in benchmark_args:
            print(f"Running {benchmark_name}")
            results.append(run_task(benchmark_name, args, timeout))
            print(f"Exit condition: {results[-1][1]}")
    else:
        total = len(benchmark_args)
        print(f"Launching {total} benchmarking tasks.")
        next_milestone = 10
        with multiprocessing.Pool(nthreads) as p:
            for i, result in enumerate(p.imap_unordered(_run_task_unpack, benchmark_args), start=1):
                results.append(result)
                pct = i * 100 // total
                if pct >= next_milestone:
                    print(f"Progress: {i}/{total} ({pct}%) complete")
                    next_milestone += 10

    results.sort()
    for _, cause, _, _ in results:
        success_cnt += 1 if cause == "success" else 0
        error_cnt += 1 if cause == "error" else 0
        timeout_cnt += 1 if cause == "timeout" else 0

    output_path = f"circom_benchmarks_results_{llzk_opt}.csv"
    with open(output_path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Benchmark", "Result", "Time Seconds", "Error Message"])
        writer.writerows(results)
    print(f"success: {success_cnt}, errored: {error_cnt}, timeout: {timeout_cnt}")
    return output_path

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description="Run circom benchmarks and collect timing results.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("--benchmark_dir", help="Path to the circom-benchmarks directory.", default=".")
    parser.add_argument("--timeout", type=float, default=2, help="Per-benchmark timeout in seconds.")
    parser.add_argument("--concrete", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--circom-bin", default="circom", help="Path to the circom binary.")
    parser.add_argument("--nthreads", type=int, default=multiprocessing.cpu_count(), help="Number of jobs to run at once.")
    args = parser.parse_args()
    start = time.time()
    print(f"{args.benchmark_dir = }")
    try:
        files = load_benchmarks(args.benchmark_dir)
    except (OSError, json.JSONDecodeError) as error:
        parser.error(f"Cannot load benchmark metadata: {error}")
    run_circom_benchmarks(
        files,
        args.benchmark_dir,
        args.timeout,
        args.concrete,
        args.circom_bin,
        args.nthreads
    )
    elapsed = datetime.timedelta(seconds=time.time() - start)
    print(f"Total benchmark execution time: {elapsed}")
