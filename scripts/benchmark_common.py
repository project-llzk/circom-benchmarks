"""Shared paths, metadata validation, and provenance for benchmark scripts."""

import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tempfile


DEFAULT_ROOT = Path(__file__).resolve().parent.parent


def resolve_root(value=DEFAULT_ROOT) -> Path:
    root = Path(value).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"Benchmark directory does not exist: {root}")
    return root


def _suite_name(root: Path) -> str:
    root = Path(root).expanduser().resolve()
    return "self" if root == DEFAULT_ROOT else root.name


def resolve_metadata_path(root: Path, value=None) -> Path:
    """Default to this repository's metadata directory; explicit paths use cwd."""
    if value is None:
        return DEFAULT_ROOT / "metadata" / f"{_suite_name(root)}.json"
    return Path(value).expanduser().resolve()


def resolve_output_dir(root: Path, value=None) -> Path:
    """Default to this repository's suite results directory; explicit paths use cwd."""
    if value is None:
        return (DEFAULT_ROOT / "benchmark_results" / _suite_name(root)).resolve()
    return Path(value).expanduser().resolve()


def load_metadata(path: Path) -> list:
    """Validate the portable manifest schema without inspecting circuit files."""
    with Path(path).open(encoding="utf-8") as handle:
        entries = json.load(handle)
    if not isinstance(entries, list):
        raise ValueError(f"Metadata must be a JSON array: {path}")
    seen = set()
    for index, entry in enumerate(entries):
        label = f"Metadata entry {index + 1} in {path}"
        if not isinstance(entry, dict):
            raise ValueError(f"{label} must be an object")
        value = entry.get("path")
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label} requires a nonempty path")
        relative = PurePosixPath(value)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or "\\" in value
            or ":" in value
            or "\x00" in value
            or relative.suffix != ".circom"
        ):
            raise ValueError(f"{label} path must be a relative .circom path within the benchmark directory: {value!r}")
        normalized = relative.as_posix()
        if normalized in seen:
            raise ValueError(f"Duplicate benchmark path in {path}: {normalized}")
        seen.add(normalized)
        entry["path"] = normalized
        if "enabled" in entry and not isinstance(entry["enabled"], bool):
            raise ValueError(f"{label} enabled must be a boolean")
        expected = entry.get("expected", "success")
        if expected not in ("success", "failure"):
            raise ValueError(f"{label} expected must be 'success' or 'failure'")
        entry["expected"] = expected
    return entries


def git_info(root: Path, exclude_paths=()) -> dict:
    """Describe a checkout, optionally excluding generated paths from its status."""
    def read(*args):
        try:
            result = subprocess.run(
                ["git", "-C", str(root), *args],
                capture_output=True, text=True, timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        return result.stdout.strip() if result.returncode == 0 else None

    toplevel = read("rev-parse", "--show-toplevel")
    if toplevel is None:
        return {"repository": "", "commit": "", "dirty": None}
    checkout = Path(toplevel).resolve()
    pathspecs = [":/"]  # Keep checking the whole checkout when root is a subdirectory.
    for path in exclude_paths:
        try:
            # Exclude the named entry, not a symlink's potentially unrelated target.
            relative = Path(path).absolute().relative_to(checkout)
        except ValueError:
            continue
        pathspecs.append(f":(top,exclude,literal){relative.as_posix()}")
    status = read("status", "--porcelain", "--untracked-files=normal", "--", *pathspecs)
    repository = read("remote", "get-url", "origin") or ""
    # Remove URL userinfo without changing scp-style remotes or local paths.
    repository = re.sub(r"^([A-Za-z][A-Za-z0-9+.-]*://)[^/?#]*@", r"\1", repository)
    return {
        "repository": repository,
        "commit": read("rev-parse", "HEAD") or "",
        "dirty": bool(status) if status is not None else None,
    }


def write_json(path: Path, data) -> None:
    """Atomically write JSON, preserving permissions when the destination exists."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(data, handle, indent=2)
            handle.write("\n")
        try:
            mode = stat.S_IMODE(path.stat().st_mode)
        except FileNotFoundError:
            pass
        else:
            temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
