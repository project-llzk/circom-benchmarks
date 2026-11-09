#!/usr/bin/env python3
"""Discover Circom entrypoints in a local source tree and write benchmark metadata."""
from __future__ import annotations

import argparse
from fnmatch import fnmatchcase
import os
from pathlib import Path
import re

from benchmark_common import (
    DEFAULT_ROOT,
    git_info,
    load_metadata,
    resolve_metadata_path,
    resolve_root,
    write_json,
)

ROOT = DEFAULT_ROOT
DEFAULT_EXCLUDED_DIRS = frozenset(
    {
        ".git", "node_modules", "llzk-outputs", "__pycache__", ".pytest_cache",
        ".venv", "target", "build", "dist", "benchmark_reports",
        ".artifacts",
    }
)
# Mask strings as well as comments so log messages and include paths cannot look
# like entrypoints or start comments. Keep whitespace between adjacent tokens.
NON_CODE_RE = re.compile(r'"(?:\\[\s\S]|[^"\\])*(?:"|\Z)|//[^\r\n]*|/\*[\s\S]*?(?:\*/|\Z)')
MAIN_RE = re.compile(r"\bcomponent\s+main\b\s*(?:\{[^{}]*\}\s*)?=", re.ASCII)


def mask_comments_and_strings(text: str) -> str:
    return NON_CODE_RE.sub(
        lambda match: "".join(c if c in "\r\n" else " " for c in match.group()),
        text,
    )


def has_component_main(path: Path) -> bool:
    """Recognize main declarations across lines, ignoring comments and strings."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ValueError(f"Cannot read circuit {path}: {error}") from error
    return MAIN_RE.search(mask_comments_and_strings(text)) is not None


def benchmark_name(path: Path, root: Path = DEFAULT_ROOT) -> str:
    rel = path.relative_to(root)
    stem = path.stem
    if stem != "main":
        return stem
    # Retain the existing display names for this repository's applications.
    parts = rel.parts
    if len(parts) >= 2 and parts[0] in {"applications", "libs", "lib"}:
        return parts[1]
    return rel.parent.name or stem


def _excluded(relative_path: Path, patterns, *, is_dir: bool) -> bool:
    """Match directories and ancestors too, including explicitly selected scopes."""
    parts = relative_path.parts if is_dir else relative_path.parts[:-1]
    if any(part in DEFAULT_EXCLUDED_DIRS for part in parts):
        return True
    candidates = [relative_path.as_posix()]
    candidates.extend(parent.as_posix() for parent in relative_path.parents if parent != Path("."))
    return any(fnmatchcase(candidate, pattern) for candidate in candidates for pattern in patterns)


def discover_circuits(root: Path, scan_dirs=None, exclude_patterns=None):
    """Yield sorted unique circuit paths without following directory symlinks.

    Exclusions use fnmatch against POSIX root-relative paths; '*' also matches
    '/'. A matching directory excludes all of its descendants.
    """
    root = resolve_root(root)
    patterns = [pattern.rstrip("/") for pattern in (exclude_patterns or [])]
    paths = set()
    for scope in scan_dirs or ["."]:
        relative_scope = Path(scope)
        if relative_scope.is_absolute():
            raise ValueError(f"Scan scope must be relative to the benchmark directory: {scope}")
        candidate = Path(os.path.abspath(root / relative_scope))
        resolved = candidate.resolve()
        if not resolved.is_relative_to(root):
            raise ValueError(f"Scan scope is outside the benchmark directory: {scope}")
        if not candidate.exists():
            raise ValueError(f"Scan scope does not exist: {candidate}")
        # An explicit symlink scope must not bypass the no-directory-symlinks rule.
        if (candidate.is_dir() and candidate.is_symlink()) or any(
            parent.is_symlink()
            for parent in candidate.parents
            if parent != root and parent.is_relative_to(root)
        ):
            continue
        if _excluded(candidate.relative_to(root), patterns, is_dir=candidate.is_dir()):
            continue
        if candidate.is_file():
            if candidate.suffix != ".circom":
                raise ValueError(f"Scan scope is not a Circom file: {candidate}")
            paths.add(candidate)
            continue
        if not candidate.is_dir():
            raise ValueError(f"Scan scope is not a directory or Circom file: {candidate}")

        def on_error(error):
            raise ValueError(f"Cannot scan benchmark directory: {error}") from error

        for directory, dirs, files in os.walk(candidate, followlinks=False, onerror=on_error):
            directory = Path(directory)
            dirs[:] = sorted(
                name for name in dirs
                if not (directory / name).is_symlink()
                and not _excluded((directory / name).relative_to(root), patterns, is_dir=True)
            )
            for filename in files:
                path = directory / filename
                if path.suffix != ".circom" or _excluded(path.relative_to(root), patterns, is_dir=False):
                    continue
                if not path.resolve().is_relative_to(root):
                    raise ValueError(f"Circuit symlink is outside the benchmark directory: {path}")
                paths.add(path)
    return sorted(paths)


def build_entries(root=DEFAULT_ROOT, metadata_path=None, scan_dirs=None, exclude_patterns=None):
    """Discover benchmarks, preserving existing settings by source-relative path."""
    root = resolve_root(root)
    metadata_path = resolve_metadata_path(root, metadata_path)
    existing = {}
    if metadata_path.exists():
        existing = {entry["path"]: entry for entry in load_metadata(metadata_path)}
    provenance = git_info(root)
    entries = []
    for path in discover_circuits(root, scan_dirs, exclude_patterns):
        if not has_component_main(path):
            continue
        relative_path = path.relative_to(root).as_posix()
        previous = existing.get(relative_path, {})
        entry = dict(previous)
        entry.update(
            {
                "benchmark name": benchmark_name(path, root),
                "path": relative_path,
                "repository": provenance["repository"],
                "commit": provenance["commit"],
                "enabled": previous.get("enabled", True),
            }
        )
        entries.append(entry)
    return entries


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        epilog="Directory names excluded at every depth: " + ", ".join(sorted(DEFAULT_EXCLUDED_DIRS)) + ". Directory symlinks are not followed.",
    )
    parser.add_argument("--benchmark_dir", "--benchmark-dir", default=str(DEFAULT_ROOT),
                        help="Local directory containing benchmark sources.")
    parser.add_argument("--metadata-file", help="Metadata output path, relative to the current directory; defaults to <benchmark_dir>/benchmark_metadata.json.")
    parser.add_argument("--scan-dir", action="append", help="Source-root-relative directory or .circom file to scan; repeat for multiple scopes. Defaults to the whole source root.")
    parser.add_argument("--exclude", action="append", default=[], help="Exclude root-relative fnmatch patterns; '*' also matches '/'. Matching directories exclude descendants. Repeat as needed.")
    args = parser.parse_args(argv)
    try:
        root = resolve_root(args.benchmark_dir)
        out_path = resolve_metadata_path(root, args.metadata_file)
        entries = build_entries(root, out_path, args.scan_dir, args.exclude)
        write_json(out_path, entries)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(out_path)
    print(f"Detected {len(entries)} benchmarks ({sum(entry['enabled'] for entry in entries)} enabled)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
