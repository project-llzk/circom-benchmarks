#!/usr/bin/env python3
"""Detect circom benchmarks with `component main` and write benchmark_metadata.json."""
from __future__ import annotations

from pathlib import Path
import json
import re
import sys

ROOT = Path(__file__).resolve().parent

# Match lines like: component main = ... or component main{public [...]} = ...
MAIN_RE = re.compile(r"\bcomponent\s+main\b.*=")
# Extract commit suffix like name-abcdef1
COMMIT_RE = re.compile(r"(.*?)-([0-9a-fA-F]{7,})$")


def strip_comments(lines):
    in_block = False
    for line in lines:
        s = line
        i = 0
        while i < len(s):
            if in_block:
                end = s.find("*/", i)
                if end == -1:
                    s = ""
                    break
                in_block = False
                i = end + 2
                continue
            start = s.find("/*", i)
            line_comment = s.find("//", i)
            if line_comment != -1 and (start == -1 or line_comment < start):
                s = s[:line_comment]
                break
            if start != -1:
                end = s.find("*/", start + 2)
                if end == -1:
                    s = s[:start]
                    in_block = True
                    break
                s = s[:start] + s[end + 2 :]
                i = start
                continue
            break
        yield s


def has_component_main(path: Path) -> bool:
    try:
        text = path.read_text(errors="ignore")
    except Exception:
        return False
    for line in strip_comments(text.splitlines()):
        if MAIN_RE.search(line):
            return True
    return False


def benchmark_name(path: Path) -> str:
    rel = path.relative_to(ROOT)
    stem = path.stem
    if stem != "main":
        return stem
    # For main.circom, use directory name under applications/ or libs/ if present.
    parts = rel.parts
    if len(parts) >= 2 and parts[0] in {"applications", "libs"}:
        return parts[1]
    if len(parts) >= 2 and parts[0] == "lib":
        return parts[1]
    if rel.parent.name:
        return rel.parent.name
    return stem


def build_entries():
    entries = []
    for path in sorted(ROOT.rglob("*.circom")):
        if not has_component_main(path):
            continue
        name = benchmark_name(path)
        repo = ""
        commit = ""
        m = COMMIT_RE.match(name)
        if m:
            repo = m.group(1)
            commit = m.group(2)
        entries.append(
            {
                "benchmark name": name,
                "path": str(path.relative_to(ROOT)),
                "repository": repo,
                "commit": commit,
            }
        )
    return entries


def main() -> int:
    entries = build_entries()
    out_path = ROOT / "benchmark_metadata.json"
    out_path.write_text(json.dumps(entries, indent=2) + "\n")
    print(out_path)
    print(len(entries))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
