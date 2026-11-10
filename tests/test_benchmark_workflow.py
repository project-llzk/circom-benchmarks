"""Regression tests for metadata-driven benchmarks in external directories."""

import contextlib
import csv
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock


REPOSITORY = Path(__file__).resolve().parents[1]
DETECTOR = REPOSITORY / "scripts" / "benchmark_detector.py"
EVALUATOR = REPOSITORY / "scripts" / "circom_to_llzk_eval.py"

with mock.patch.object(sys, "path", [str(REPOSITORY / "scripts"), *sys.path]):
    import benchmark_common
    import benchmark_detector
    import circom_to_llzk_eval


class ExternalBenchmarkWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="circom-workflow-")
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name).resolve()
        self.root = self.workspace / "source tree"
        self.caller = self.workspace / "unrelated caller"
        self.root.mkdir()
        self.caller.mkdir()
        self.metadata = self.caller / "metadata" / "suite.json"
        self.output = self.caller / "results"
        self.compiler = self.workspace / "fake-circom"
        self.compiler.write_text(
            f"#!{sys.executable}\n"
            + textwrap.dedent(
                f"""\
                import json
                import os
                from pathlib import Path
                import signal
                import sys
                import time

                if sys.argv[1:] == ["--version"]:
                    print("circom compiler test fixture")
                    raise SystemExit(0)

                args = sys.argv[1:]
                assert Path.cwd() == Path({str(self.root)!r}), Path.cwd()
                source = Path(args[-1])
                assert source.is_absolute() and source.is_file(), source
                output = Path(args[args.index("-o") + 1])
                assert output.is_absolute() and output.is_dir(), output
                includes = [args[i + 1] for i, arg in enumerate(args) if arg == "-l"]
                assert all(Path(p).is_absolute() and Path(p).is_dir() for p in includes), includes
                if "FAIL_COMPILATION" in source.read_text():
                    print("fixture compilation error", file=sys.stderr)
                    raise SystemExit(1)
                if "PANIC_COMPILATION" in source.read_text():
                    print("fixture compiler panic", file=sys.stderr)
                    raise SystemExit(101)
                if "SIGNAL_COMPILATION" in source.read_text():
                    os.kill(os.getpid(), signal.SIGTERM)
                if "SLOW_COMPILATION" in source.read_text():
                    time.sleep(2)
                (output / "invocation.json").write_text(json.dumps({{
                    "source": str(source), "cwd": str(Path.cwd()),
                    "includes": includes, "args": args,
                }}))
                (output / (source.stem + ".llzk")).write_text("fixture output\\n")
                """
            )
        )
        self.compiler.chmod(0o755)

    def circuit(self, path, contents="component main = Example();\n"):
        source = self.root / path
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(contents)
        return source

    def write_metadata(self, entries):
        self.metadata.parent.mkdir(parents=True, exist_ok=True)
        self.metadata.write_text(json.dumps(entries))

    def run_script(self, script, *arguments, expected=0):
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        result = subprocess.run(
            [sys.executable, str(script), *map(str, arguments)],
            cwd=self.caller,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(
            result.returncode,
            expected,
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        return result

    def detect(self, *arguments, expected=0):
        return self.run_script(
            DETECTOR,
            "--benchmark_dir", "../source tree",
            "--metadata-file", "metadata/suite.json",
            *arguments,
            expected=expected,
        )

    def evaluate(self, *arguments, expected=0):
        return self.run_script(
            EVALUATOR,
            "--benchmark-dir", "../source tree",
            "--metadata-file", "metadata/suite.json",
            "--output-dir", "results",
            "--circom-bin", self.compiler,
            "--nthreads", "1",
            *arguments,
            expected=expected,
        )

    def read_results(self, mode="concrete"):
        path = self.output / mode / f"circom_benchmarks_results_{mode}.csv"
        with path.open(newline="") as handle:
            return list(csv.DictReader(handle))

    def invocations(self, mode="concrete"):
        return [
            json.loads(path.read_text())
            for path in (self.output / mode).rglob("invocation.json")
        ]

    def test_detector_handles_comments_strings_multiline_and_default_exclusions(self):
        self.circuit("normal.circom")
        self.circuit(
            "multiline.circom",
            "component /* comment */\n main\n { public [x] }\n = Example();\n",
        )
        self.circuit("block_end.circom", "/* comment\n*/ component main = Example();\n")
        self.circuit("commented.circom", "/*\ncomponent main = Example(); */\n")
        self.circuit("line_comment.circom", "// component main = Example();\n")
        self.circuit("quoted.circom", 'log("component main = Example();");\n')
        self.circuit(
            "comment_tokens_in_string.circom",
            'log("http://host/*text*/");\ncomponent main = Example();\n',
        )
        self.circuit("library.circom", "template Example() {}\n")
        self.circuit("fails/example.circom")
        self.circuit("tags/outputs/main.circom")
        for directory in (".git", "node_modules", "llzk_outputs", "__pycache__"):
            self.circuit(f"{directory}/ignored.circom")
        outside = self.workspace / "linked sources"
        outside.mkdir()
        (outside / "ignored.circom").write_text("component main = Example();\n")
        (self.root / "linked").symlink_to(outside, target_is_directory=True)

        self.detect()
        entries = json.loads(self.metadata.read_text())
        self.assertEqual(
            {entry["path"] for entry in entries},
            {
                "normal.circom", "multiline.circom", "block_end.circom",
                "comment_tokens_in_string.circom", "fails/example.circom",
                "tags/outputs/main.circom",
            },
        )
        self.assertTrue(all(entry["enabled"] is True for entry in entries))
        self.assertTrue(all(entry["expected"] == "success" for entry in entries))
        self.assertFalse((self.root / "benchmark_metadata.json").exists())

    def test_detector_preserves_toggles_and_upgrades_legacy_entries(self):
        self.circuit("first/main.circom")
        self.circuit("second/main.circom")
        self.write_metadata([
            {"path": "first/main.circom", "benchmark name": "same", "enabled": False, "expected": "failure"},
            {"path": "second/main.circom", "benchmark name": "same"},
        ])
        self.circuit("new.circom")
        self.detect()
        entries = json.loads(self.metadata.read_text())
        self.assertEqual(
            {entry["path"]: entry["enabled"] for entry in entries},
            {"first/main.circom": False, "second/main.circom": True, "new.circom": True},
        )
        self.assertEqual(
            {entry["path"]: entry["expected"] for entry in entries},
            {"first/main.circom": "failure", "second/main.circom": "success", "new.circom": "success"},
        )
        once = self.metadata.read_bytes()
        self.detect()
        self.assertEqual(self.metadata.read_bytes(), once)

    def test_detector_scopes_and_excludes(self):
        for path in ("chosen/a.circom", "chosen/excluded/b.circom", "other/c.circom"):
            self.circuit(path)
        self.detect("--scan-dir", "chosen", "--exclude", "chosen/excluded")
        self.assertEqual(
            [entry["path"] for entry in json.loads(self.metadata.read_text())],
            ["chosen/a.circom"],
        )
        self.detect("--scan-dir", "chosen/a.circom", "--scan-dir", "other/c.circom")
        self.assertEqual(
            [entry["path"] for entry in json.loads(self.metadata.read_text())],
            ["chosen/a.circom", "other/c.circom"],
        )

    def test_default_metadata_is_shared_outside_the_source_and_caller_directories(self):
        self.circuit("main.circom")
        tool_root = self.workspace / "benchmark tools"
        tool_root.mkdir()
        expected_metadata = tool_root / "metadata" / f"{self.root.name}.json"
        original_directory = Path.cwd()
        try:
            os.chdir(self.caller)
            with mock.patch.object(benchmark_common, "DEFAULT_ROOT", tool_root), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(benchmark_detector.main(["--benchmark-dir", str(self.root)]), 0)
                self.assertTrue(expected_metadata.is_file())
                self.assertEqual(circom_to_llzk_eval.main([
                    "--benchmark-dir", str(self.root),
                    "--output-dir", "results",
                    "--circom-bin", str(self.compiler),
                    "--nthreads", "1",
                ]), 0)
        finally:
            os.chdir(original_directory)

        self.assertEqual([row["Benchmark"] for row in self.read_results()], ["main.circom"])
        info = json.loads((self.output / "concrete" / "run_info.json").read_text())
        self.assertEqual(info["metadata_file"], str(expected_metadata))
        self.assertFalse((self.root / "benchmark_metadata.json").exists())
        self.assertFalse((self.root / "metadata").exists())
        self.assertFalse((self.caller / "metadata").exists())

    def test_metadata_defaults_for_self_external_sources_and_explicit_override(self):
        tool_root = self.workspace / "benchmark tools"
        tool_root.mkdir()
        original_directory = Path.cwd()
        try:
            os.chdir(self.caller)
            with mock.patch.object(benchmark_common, "DEFAULT_ROOT", tool_root):
                self.assertEqual(
                    benchmark_common.resolve_metadata_path(tool_root),
                    tool_root / "metadata" / "self.json",
                )
                self.assertEqual(
                    benchmark_common.resolve_metadata_path(self.root),
                    tool_root / "metadata" / f"{self.root.name}.json",
                )
                self.assertEqual(
                    benchmark_common.resolve_metadata_path(self.root, "metadata/custom.json"),
                    self.caller / "metadata" / "custom.json",
                )
                self.assertEqual(
                    benchmark_common.resolve_metadata_path(self.root, self.metadata),
                    self.metadata,
                )
        finally:
            os.chdir(original_directory)

    def test_invalid_metadata_is_not_overwritten(self):
        self.circuit("main.circom")
        self.metadata.parent.mkdir(parents=True, exist_ok=True)
        self.metadata.write_text("broken JSON")
        self.detect(expected=2)
        self.assertEqual(self.metadata.read_text(), "broken JSON")

    def test_detector_rejects_scan_scopes_outside_source_root(self):
        outside = self.workspace / "outside"
        outside.mkdir()
        (outside / "main.circom").write_text("component main = Example();\n")
        result = self.detect("--scan-dir", "../outside", expected=2)
        self.assertIn("error:", result.stderr)
        self.assertFalse(self.metadata.exists())

    def test_evaluator_uses_only_enabled_metadata_and_configured_includes(self):
        self.circuit("listed.circom", "template NoMain() {}\n")
        self.circuit("disabled.circom")
        self.circuit("missing_flag.circom")
        self.circuit("unlisted.circom")
        self.write_metadata([
            {"path": "listed.circom", "enabled": True},
            {"path": "disabled.circom", "enabled": False},
            {"path": "missing_flag.circom"},
            {"path": "removed.circom", "enabled": False},
        ])
        relative_include = self.root / "library includes"
        relative_include.mkdir()
        absolute_include = self.workspace / "external includes"
        absolute_include.mkdir()
        self.evaluate("-l", "library includes", "--include-dir", absolute_include)

        rows = self.read_results()
        self.assertEqual([(row["Benchmark"], row["Result"]) for row in rows], [("listed.circom", "success")])
        invocations = self.invocations()
        self.assertEqual(len(invocations), 1)
        self.assertEqual(invocations[0]["source"], str(self.root / "listed.circom"))
        self.assertEqual(invocations[0]["cwd"], str(self.root))
        self.assertEqual(invocations[0]["includes"], [str(relative_include), str(absolute_include)])
        info = json.loads((self.output / "concrete" / "run_info.json").read_text())
        self.assertEqual(info["benchmark_dir"], str(self.root))
        self.assertEqual(info["metadata_file"], str(self.metadata))
        self.assertEqual(info["compiler"]["path"], str(self.compiler))
        self.assertIn("test fixture", info["compiler"]["version"])
        self.assertEqual([entry["path"] for entry in info["benchmarks"]], ["listed.circom"])
        self.assertFalse((self.caller / "llzk_outputs").exists())

    def test_output_paths_and_modes_do_not_collide(self):
        sources = ["a/b.circom", "a_b.circom", "a.circom"]
        for path in sources:
            self.circuit(path)
        self.write_metadata([{"path": path, "enabled": True} for path in sources])
        for mode, flag in (("concrete", "--concrete"), ("templated", "--no-concrete")):
            with self.subTest(mode=mode):
                self.evaluate(flag, "--nthreads", "2")
                self.assertEqual(
                    {row["Benchmark"] for row in self.read_results(mode)}, set(sources)
                )
                self.assertTrue(all(row["Result"] == "success" for row in self.read_results(mode)))
                invocations = self.invocations(mode)
                self.assertEqual({item["source"] for item in invocations}, {str(self.root / path) for path in sources})
                self.assertEqual(len(invocations), len(sources))
                self.assertTrue(all(f"--llzk={mode}" in item["args"] for item in invocations))

    def test_evaluator_reports_per_benchmark_compilation_errors(self):
        self.circuit("good.circom")
        self.circuit("bad.circom", "FAIL_COMPILATION\n")
        self.write_metadata([
            {"path": "good.circom", "enabled": True},
            {"path": "bad.circom", "enabled": True},
        ])
        self.evaluate(expected=1)
        rows = {row["Benchmark"]: row for row in self.read_results()}
        self.assertEqual(rows["good.circom"]["Result"], "success")
        self.assertEqual(rows["bad.circom"]["Result"], "error")
        self.assertIn("fixture compilation error", rows["bad.circom"]["Error Message"])
        self.assertEqual(rows["bad.circom"]["Expected"], "success")
        self.assertEqual(rows["bad.circom"]["Expectation"], "mismatch")
        self.assertEqual(rows["bad.circom"]["Exit Code"], "1")

    def test_expected_outcomes_match_and_mismatch_in_serial_and_parallel_runs(self):
        cases = {
            "matching_success.circom": ("success", "success", "match", "0"),
            "matching_failure.circom": ("failure", "error", "match", "1"),
            "unexpected_success.circom": ("failure", "success", "mismatch", "0"),
            "unexpected_failure.circom": ("success", "error", "mismatch", "1"),
        }
        for path, (_, actual, _, _) in cases.items():
            self.circuit(path, "FAIL_COMPILATION\n" if actual == "error" else "component main = Example();\n")
        self.write_metadata([
            {"path": path, "enabled": True, "expected": expected}
            for path, (expected, _, _, _) in cases.items()
        ])
        for workers in (1, 2):
            with self.subTest(workers=workers):
                result = self.evaluate("--nthreads", str(workers), expected=1)
                rows = {row["Benchmark"]: row for row in self.read_results()}
                self.assertEqual(set(rows), set(cases))
                for path, (expected, actual, assessment, exit_code) in cases.items():
                    row = rows[path]
                    self.assertEqual(
                        (row["Expected"], row["Result"], row["Expectation"], row["Exit Code"]),
                        (expected, actual, assessment, exit_code),
                    )
                self.assertIn("fixture compilation error", rows["matching_failure.circom"]["Error Message"])
                self.assertIn("unexpected_success.circom", result.stdout)
                self.assertIn("unexpected_failure.circom", result.stdout)
                info = json.loads((self.output / "concrete" / "run_info.json").read_text())
                self.assertEqual(info["results_summary"]["matched"], 2)
                self.assertEqual(info["results_summary"]["mismatched"], 2)
                self.assertEqual(info["results_summary"]["success"], 2)
                self.assertEqual(info["results_summary"]["error"], 2)

    def test_expected_compilation_failures_exit_successfully(self):
        self.circuit("invalid.circom", "FAIL_COMPILATION\n")
        self.circuit("valid.circom")
        self.write_metadata([
            {"path": "invalid.circom", "enabled": True, "expected": "failure"},
            {"path": "valid.circom", "enabled": True, "expected": "success"},
        ])
        self.evaluate("--nthreads", "2")
        rows = self.read_results()
        self.assertEqual([row["Expectation"] for row in rows], ["match", "match"])
        self.assertEqual([row["Result"] for row in rows], ["error", "success"])
        info = json.loads((self.output / "concrete" / "run_info.json").read_text())
        self.assertEqual(info["results_summary"]["mismatched"], 0)
        self.assertEqual(info["results_summary"]["matched"], 2)

    def test_crashes_do_not_satisfy_expected_failure(self):
        self.circuit("panic.circom", "PANIC_COMPILATION\n")
        self.circuit("signal.circom", "SIGNAL_COMPILATION\n")
        self.write_metadata([
            {"path": path, "enabled": True, "expected": "failure"}
            for path in ("panic.circom", "signal.circom")
        ])
        self.evaluate(expected=1)
        rows = {row["Benchmark"]: row for row in self.read_results()}
        for row in rows.values():
            self.assertEqual(row["Result"], "error")
            self.assertEqual(row["Expectation"], "mismatch")
        self.assertEqual(rows["panic.circom"]["Exit Code"], "101")
        self.assertLess(int(rows["signal.circom"]["Exit Code"]), 0)

    def test_launch_errors_do_not_satisfy_expected_failure(self):
        source = self.circuit("main.circom")
        entries = [{"path": "main.circom", "enabled": True, "expected": "failure"}]
        with contextlib.redirect_stdout(io.StringIO()):
            circom_to_llzk_eval.run_circom_benchmarks(
                [str(source)], self.root, 1, True,
                str(self.workspace / "missing-executable"), 1,
                output_dir=self.output, metadata_entries=entries,
            )
        row = self.read_results()[0]
        self.assertEqual(row["Result"], "error")
        self.assertEqual(row["Expectation"], "mismatch")
        self.assertEqual(row["Exit Code"], "")

    def test_failed_rerun_removes_prior_successful_artifacts(self):
        source = self.circuit("main.circom")
        self.write_metadata([{"path": "main.circom", "enabled": True}])
        self.evaluate()
        self.assertEqual(len(list(self.output.rglob("*.llzk"))), 1)

        source.write_text("FAIL_COMPILATION\n")
        self.evaluate(expected=1)
        self.assertEqual(self.read_results()[0]["Result"], "error")
        self.assertEqual(list(self.output.rglob("*.llzk")), [])

    def test_disabling_benchmark_removes_prior_artifacts(self):
        for path in ("keep.circom", "disable.circom"):
            self.circuit(path)
        entries = [
            {"path": "keep.circom", "enabled": True},
            {"path": "disable.circom", "enabled": True},
        ]
        self.write_metadata(entries)
        self.evaluate()
        self.assertEqual(len(list(self.output.rglob("*.llzk"))), 2)

        entries[1]["enabled"] = False
        self.write_metadata(entries)
        self.evaluate()
        self.assertEqual([row["Benchmark"] for row in self.read_results()], ["keep.circom"])
        self.assertEqual([path.name for path in self.output.rglob("*.llzk")], ["keep.llzk"])
        self.assertEqual([item["source"] for item in self.invocations()], [str(self.root / "keep.circom")])

    def test_metadata_output_collision_is_rejected_without_overwriting(self):
        self.circuit("main.circom")
        collision = self.output / "concrete" / "run_info.json"
        collision.parent.mkdir(parents=True)
        collision.write_text(json.dumps([{"path": "main.circom", "enabled": True}]))
        original = collision.read_bytes()

        self.evaluate("--metadata-file", collision, expected=2)
        self.assertEqual(collision.read_bytes(), original)
        self.assertEqual(self.invocations(), [])

    def test_unknown_artifact_directory_is_preserved(self):
        self.circuit("main.circom")
        self.write_metadata([{"path": "main.circom", "enabled": True}])
        sentinel = self.output / "concrete" / "circuits" / "user-file.txt"
        sentinel.parent.mkdir(parents=True)
        sentinel.write_text("keep this file\n")

        self.evaluate(expected=2)
        self.assertEqual(sentinel.read_text(), "keep this file\n")
        self.assertFalse((self.output / "concrete" / "run_info.json").exists())

    def test_disabled_source_in_managed_artifacts_is_preserved(self):
        relative_source = "output/concrete/circuits/disabled.circom"
        source = self.circuit(relative_source)
        self.write_metadata([{"path": relative_source, "enabled": False}])
        output = self.root / "output"
        info_path = output / "concrete" / "run_info.json"
        info_path.write_text(json.dumps({"artifact_layout_version": 1}))
        original_source = source.read_bytes()
        original_info = info_path.read_bytes()

        self.evaluate("--output-dir", output, expected=2)
        self.assertEqual(source.read_bytes(), original_source)
        self.assertEqual(info_path.read_bytes(), original_info)

    def test_symlink_artifact_directory_is_rejected(self):
        self.circuit("main.circom")
        self.write_metadata([{"path": "main.circom", "enabled": True}])
        outside = self.workspace / "unrelated artifacts"
        outside.mkdir()
        sentinel = outside / "user-file.txt"
        sentinel.write_text("keep this file\n")
        mode_dir = self.output / "concrete"
        mode_dir.mkdir(parents=True)
        (mode_dir / "circuits").symlink_to(outside, target_is_directory=True)

        self.evaluate(expected=2)
        self.assertEqual(sentinel.read_text(), "keep this file\n")
        self.assertEqual(list(outside.iterdir()), [sentinel])

    def test_evaluator_records_timeout_and_continues(self):
        self.circuit("a_slow.circom", "SLOW_COMPILATION\n")
        self.circuit("z_fast.circom")
        for expected_outcome in ("success", "failure"):
            with self.subTest(expected_outcome=expected_outcome):
                self.write_metadata([
                    {"path": "a_slow.circom", "enabled": True, "expected": expected_outcome},
                    {"path": "z_fast.circom", "enabled": True},
                ])
                self.evaluate("--timeout", "0.5", expected=1)
                rows = {row["Benchmark"]: row for row in self.read_results()}
                self.assertEqual(rows["a_slow.circom"]["Result"], "timeout")
                self.assertEqual(rows["a_slow.circom"]["Expectation"], "mismatch")
                self.assertEqual(rows["a_slow.circom"]["Exit Code"], "")
                self.assertEqual(rows["z_fast.circom"]["Result"], "success")
                self.assertIn("timeout", rows["a_slow.circom"]["Error Message"])

    def test_evaluator_rejects_source_symlink_outside_root(self):
        outside = self.workspace / "outside.circom"
        outside.write_text("component main = Example();\n")
        (self.root / "linked.circom").symlink_to(outside)
        self.write_metadata([{"path": "linked.circom", "enabled": True}])
        result = self.evaluate(expected=2)
        self.assertIn("error:", result.stderr)
        self.assertEqual(self.invocations(), [])

    @unittest.skipUnless(shutil.which("git"), "Git is required for provenance tests")
    def test_git_provenance_records_revision_and_dirty_state(self):
        source = self.circuit("main.circom")

        def git(*arguments):
            return subprocess.check_output(
                [
                    "git", "-C", str(self.root),
                    "-c", "user.name=Benchmark Test",
                    "-c", "user.email=benchmark-test@example.invalid",
                    "-c", "commit.gpgsign=false",
                    "-c", "core.hooksPath=/dev/null",
                    *arguments,
                ],
                text=True,
                stderr=subprocess.PIPE,
                timeout=10,
            ).strip()

        git("init", "--quiet")
        origin = "https://example.invalid/benchmark-fixture.git"
        git("remote", "add", "origin", origin)
        git("add", "main.circom")
        git("commit", "--quiet", "-m", "Fixture circuit")
        revision = git("rev-parse", "HEAD")
        self.detect()
        entries = json.loads(self.metadata.read_text())
        self.assertEqual(entries[0]["repository"], origin)
        self.assertEqual(entries[0]["commit"], revision)
        self.evaluate()
        info_path = self.output / "concrete" / "run_info.json"
        clean_info = json.loads(info_path.read_text())
        self.assertEqual(clean_info["source"]["repository"], origin)
        self.assertEqual(clean_info["source"]["commit"], revision)
        self.assertIs(clean_info["source"]["dirty"], False)

        source.write_text(source.read_text() + "// local edit\n")
        self.evaluate()
        dirty_info = json.loads(info_path.read_text())
        self.assertEqual(dirty_info["source"]["commit"], revision)
        self.assertIs(dirty_info["source"]["dirty"], True)

    @unittest.skipUnless(shutil.which("git"), "Git is required for provenance tests")
    def test_git_provenance_strips_url_userinfo(self):
        self.circuit("main.circom")

        def git(*arguments):
            subprocess.run(
                ["git", "-C", str(self.root), *arguments],
                check=True, capture_output=True, text=True, timeout=10,
            )

        git("init", "--quiet")
        git("remote", "add", "origin", "https://example.invalid/repo.git")
        cases = [
            ("https://user:fake-token@example.invalid/repo.git", "https://example.invalid/repo.git"),
            ("https://fake-token@example.invalid/repo.git", "https://example.invalid/repo.git"),
            ("http://user:fake%40token@[::1]:8080/repo.git", "http://[::1]:8080/repo.git"),
            ("https://example.invalid/repo@ref.git", "https://example.invalid/repo@ref.git"),
            ("ssh://git@example.invalid:2222/repo.git", "ssh://example.invalid:2222/repo.git"),
            ("git@example.invalid:repo.git", "git@example.invalid:repo.git"),
            ("/local/repo@ref.git", "/local/repo@ref.git"),
        ]
        for origin, expected in cases:
            with self.subTest(origin=origin):
                git("remote", "set-url", "origin", origin)
                self.detect()
                self.evaluate()
                entries = json.loads(self.metadata.read_text())
                info = json.loads((self.output / "concrete" / "run_info.json").read_text())
                self.assertEqual(entries[0]["repository"], expected)
                self.assertEqual(info["source"]["repository"], expected)
                self.assertEqual(info["benchmarks"][0]["repository"], expected)

    def test_evaluator_rejects_invalid_metadata_before_compiling(self):
        self.circuit("valid.circom")
        invalid_cases = [
            {"path": "valid.circom", "enabled": True},
            ["valid.circom"],
            [{"enabled": True}],
            [{"path": "valid.circom", "enabled": "true"}],
            [{"path": "valid.circom", "enabled": 1}],
            [{"path": "valid.circom", "enabled": True, "expected": "error"}],
            [{"path": "valid.circom", "enabled": True, "expected": True}],
            [{"path": "valid.circom", "enabled": True, "expected": None}],
            [{"path": "valid.circom", "enabled": True, "expected": ["success"]}],
            [{"path": "../escape.circom", "enabled": True}],
            [{"path": str(self.root / "valid.circom"), "enabled": True}],
            [{"path": "valid.circom", "enabled": True}, {"path": "valid.circom", "enabled": False}],
            [{"path": "missing.circom", "enabled": True}],
        ]
        for entries in invalid_cases:
            with self.subTest(entries=entries):
                self.write_metadata(entries)
                result = self.evaluate(expected=2)
                self.assertIn("error:", result.stderr)
                self.assertEqual(self.invocations(), [])

    def test_missing_and_invalid_json_are_clear_errors(self):
        for contents in (None, "not JSON"):
            with self.subTest(contents=contents):
                if contents is not None:
                    self.metadata.parent.mkdir(parents=True, exist_ok=True)
                    self.metadata.write_text(contents)
                result = self.evaluate(expected=2)
                self.assertIn("error:", result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_zero_enabled_benchmarks_is_valid(self):
        self.write_metadata([{"path": "removed.circom", "enabled": False}])
        self.evaluate("--nthreads", "2")
        self.assertEqual(self.read_results(), [])
        self.assertEqual(self.invocations(), [])

    def test_invalid_run_options_fail_before_compiling(self):
        self.circuit("main.circom")
        self.write_metadata([{"path": "main.circom", "enabled": True}])
        for arguments in (
            ("--nthreads", "0"),
            ("--timeout", "0"),
            ("--include-dir", "missing library"),
            ("--circom-bin", str(self.workspace / "missing-compiler")),
        ):
            with self.subTest(arguments=arguments):
                result = self.evaluate(*arguments, expected=2)
                self.assertIn("error:", result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertEqual(self.invocations(), [])


if __name__ == "__main__":
    unittest.main()
