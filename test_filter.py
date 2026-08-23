#!/usr/bin/env python3
"""Unit tests for filter.py — stdlib unittest only, no network required.

Run: python3 test_filter.py
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock as mock
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import filter as flt  # noqa: E402


class TempRepoTestCase(unittest.TestCase):
    """Base: creates a temp dir, chdirs into it, resets filter.ROOT to match."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig_cwd = os.getcwd()
        os.chdir(self.tmpdir)
        self._orig_root = flt.ROOT
        flt.ROOT = os.path.realpath(self.tmpdir)
        self._orig_log_path = flt.LOG_PATH
        flt.LOG_PATH = os.path.join(self.tmpdir, "usage.json")

    def tearDown(self):
        os.chdir(self._orig_cwd)
        flt.ROOT = self._orig_root
        flt.LOG_PATH = self._orig_log_path
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def write(self, relpath, content):
        full = os.path.join(self.tmpdir, relpath)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        return full


class TestConfineToRoot(TempRepoTestCase):
    def test_file_inside_root_allowed(self):
        self.write("a.txt", "hi")
        self.assertEqual(flt.confine_to_root("a.txt"), os.path.join(flt.ROOT, "a.txt"))

    def test_subdir_allowed(self):
        self.write("sub/a.txt", "hi")
        self.assertEqual(flt.confine_to_root("sub"), os.path.join(flt.ROOT, "sub"))

    def test_parent_traversal_rejected(self):
        with self.assertRaises(SystemExit):
            flt.confine_to_root("../")

    def test_absolute_outside_root_rejected(self):
        with self.assertRaises(SystemExit):
            flt.confine_to_root("/etc")

    def test_root_itself_allowed(self):
        self.assertEqual(flt.confine_to_root("."), flt.ROOT)

    def test_symlink_pointing_outside_root_rejected(self):
        outside = tempfile.mkdtemp()
        try:
            link = os.path.join(self.tmpdir, "escape")
            os.symlink(outside, link)
            with self.assertRaises(SystemExit):
                flt.confine_to_root("escape")
        finally:
            shutil.rmtree(outside, ignore_errors=True)


class TestGrepSearch(TempRepoTestCase):
    def test_finds_matches_with_line_numbers(self):
        self.write("a.js", "const ServiceOrder = 1;\nconst other = 2;\n")
        matches = flt.grep_search(flt.ROOT, "ServiceOrder")
        self.assertEqual(len(matches), 1)
        self.assertIn("a.js:1:", matches[0])

    def test_case_insensitive(self):
        self.write("a.js", "SERVICEORDER\n")
        self.assertEqual(flt.grep_search(flt.ROOT, "serviceorder"), [])
        matches = flt.grep_search(flt.ROOT, "serviceorder", ignore_case=True)
        self.assertEqual(len(matches), 1)

    def test_skips_excluded_dirs(self):
        self.write("node_modules/lib.js", "ServiceOrder")
        self.write("app/a.js", "ServiceOrder")
        matches = flt.grep_search(flt.ROOT, "ServiceOrder")
        self.assertEqual(len(matches), 1)
        self.assertIn("app/a.js", matches[0])

    def test_hits_match_cap_and_warns(self):
        self.write("big.txt", "\n".join(["ServiceOrder"] * (flt.MAX_GREP_MATCHES + 50)))
        with mock.patch("sys.stderr", new_callable=io.StringIO) as stderr:
            matches = flt.grep_search(flt.ROOT, "ServiceOrder")
        self.assertEqual(len(matches), flt.MAX_GREP_MATCHES)
        self.assertIn(f"hit {flt.MAX_GREP_MATCHES}-match cap", stderr.getvalue())

    def test_no_matches_returns_empty_list(self):
        self.write("a.js", "nothing here")
        self.assertEqual(flt.grep_search(flt.ROOT, "ServiceOrder"), [])

    def test_binary_file_skipped_without_crash(self):
        with open(os.path.join(self.tmpdir, "bin.dat"), "wb") as f:
            f.write(b"\xff\xfe\x00\x01ServiceOrder")
        matches = flt.grep_search(flt.ROOT, "ServiceOrder")
        self.assertEqual(matches, [])

    def test_stats_reports_chars_scanned(self):
        self.write("a.js", "const ServiceOrder = 1;\nconst other = 2;\n")
        stats = {}
        flt.grep_search(flt.ROOT, "ServiceOrder", stats=stats)
        self.assertEqual(stats["chars_scanned"], len("const ServiceOrder = 1;\n") + len("const other = 2;\n"))

    def test_single_file_input_is_searched(self):
        full = self.write("sub/a.js", "const ServiceOrder = 1;\nconst other = 2;\n")
        matches = flt.grep_search(full, "ServiceOrder")
        self.assertEqual(matches, ["a.js:1: const ServiceOrder = 1;"])

    def test_single_file_input_no_matches(self):
        full = self.write("a.js", "nothing here\n")
        self.assertEqual(flt.grep_search(full, "ServiceOrder"), [])


class TestFindSearch(TempRepoTestCase):
    def test_finds_files_by_glob(self):
        self.write("src/app.js", "x")
        self.write("README.md", "x")
        matches = flt.find_search(flt.ROOT, "*.js")
        self.assertEqual(matches, ["src/app.js"])

    def test_finds_dirs_with_trailing_slash(self):
        self.write("fixtures/dump.js", "x")
        matches = flt.find_search(flt.ROOT, "fixtures")
        self.assertIn("fixtures/", matches)

    def test_ignore_case(self):
        self.write("README.md", "x")
        self.assertEqual(flt.find_search(flt.ROOT, "readme*"), [])
        self.assertEqual(flt.find_search(flt.ROOT, "readme*", ignore_case=True), ["README.md"])

    def test_skips_excluded_dirs(self):
        self.write("node_modules/pkg.js", "x")
        self.write("src/app.js", "x")
        matches = flt.find_search(flt.ROOT, "*.js")
        self.assertEqual(matches, ["src/app.js"])

    def test_custom_excluded_dirs(self):
        self.write("fixtures/dump.js", "x")
        self.write("src/app.js", "x")
        matches = flt.find_search(flt.ROOT, "*.js", excluded_dirs={"fixtures"})
        self.assertEqual(matches, ["src/app.js"])

    def test_no_matches_returns_empty_list(self):
        self.write("README.md", "x")
        self.assertEqual(flt.find_search(flt.ROOT, "*.js"), [])


class TestProjectExcludes(TempRepoTestCase):
    def test_no_config_returns_empty_set(self):
        self.assertEqual(flt.load_project_excludes(flt.ROOT), set())

    def test_reads_exclude_list(self):
        self.write(".claude/local-context-filter.json", json.dumps({"exclude": ["fixtures", "vendor"]}))
        self.assertEqual(flt.load_project_excludes(flt.ROOT), {"fixtures", "vendor"})

    def test_invalid_json_returns_empty_set(self):
        self.write(".claude/local-context-filter.json", "not json")
        self.assertEqual(flt.load_project_excludes(flt.ROOT), set())

    def test_non_list_exclude_returns_empty_set(self):
        self.write(".claude/local-context-filter.json", json.dumps({"exclude": "fixtures"}))
        self.assertEqual(flt.load_project_excludes(flt.ROOT), set())


class TestGenerateReport(TempRepoTestCase):
    def test_no_log_file_returns_sentinel(self):
        self.assertEqual(flt.generate_report(flt.LOG_PATH), "NO_USAGE_DATA")

    def test_empty_log_file_returns_sentinel(self):
        open(flt.LOG_PATH, "w").close()
        self.assertEqual(flt.generate_report(flt.LOG_PATH), "NO_USAGE_DATA")

    def test_aggregates_by_mode(self):
        flt.log_usage("grep", None, 100, 20)
        flt.log_usage("grep", None, 200, 40)
        flt.log_usage("ls", None, 10, 10)
        report = flt.generate_report(flt.LOG_PATH)
        self.assertIn("3 runs", report)
        self.assertIn("grep", report)
        self.assertIn("2 runs", report)
        self.assertIn("Total tokens saved (est.): 60", report)

    def test_skips_malformed_lines(self):
        with open(flt.LOG_PATH, "w", encoding="utf-8") as f:
            f.write("not json\n")
            f.write(json.dumps({"ts": "t", "mode": "grep", "chars_in": 100, "chars_out": 20, "tokens_saved_est": 20}) + "\n")
        report = flt.generate_report(flt.LOG_PATH)
        self.assertIn("1 runs", report)


class TestLogUsage(TempRepoTestCase):
    def test_appends_one_json_line_with_expected_fields(self):
        flt.log_usage("grep", None, 100, 20)
        with open(flt.LOG_PATH, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
        self.assertEqual(len(lines), 1)
        entry = json.loads(lines[0])
        self.assertEqual(entry["mode"], "grep")
        self.assertIsNone(entry["backend"])
        self.assertEqual(entry["chars_in"], 100)
        self.assertEqual(entry["chars_out"], 20)
        self.assertEqual(entry["tokens_saved_est"], 20)
        self.assertIn("ts", entry)

    def test_never_negative_savings(self):
        flt.log_usage("task", "ollama", 10, 50)
        with open(flt.LOG_PATH, "r", encoding="utf-8") as f:
            entry = json.loads(f.read().splitlines()[0])
        self.assertEqual(entry["tokens_saved_est"], 0)

    def test_write_failure_does_not_raise(self):
        flt.LOG_PATH = os.path.join(self.tmpdir, "nonexistent-dir", "usage.json")
        flt.log_usage("ls", None, 10, 10)  # should not raise

    def test_log_stays_under_trigger_untouched(self):
        for i in range(10):
            flt.log_usage("grep", None, 10, 5)
        with open(flt.LOG_PATH, "r", encoding="utf-8") as f:
            self.assertEqual(len(f.readlines()), 10)

    def test_log_rotates_once_over_trigger_keeping_most_recent(self):
        with open(flt.LOG_PATH, "w", encoding="utf-8") as f:
            for i in range(flt.USAGE_LOG_TRIM_TRIGGER):
                f.write(json.dumps({"n": i}) + "\n")
        flt.log_usage("grep", None, 10, 5)  # pushes it over the trigger
        with open(flt.LOG_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
        self.assertEqual(len(lines), flt.USAGE_LOG_KEEP_LINES)
        self.assertEqual(json.loads(lines[0])["n"], flt.USAGE_LOG_TRIM_TRIGGER - flt.USAGE_LOG_KEEP_LINES + 1)
        self.assertEqual(json.loads(lines[-1])["mode"], "grep")


class TestRotateUsageLog(TempRepoTestCase):
    def test_missing_file_is_a_noop(self):
        flt.rotate_usage_log_if_needed(os.path.join(self.tmpdir, "does-not-exist.json"))  # should not raise

    def test_trims_to_keep_lines_when_over_trigger(self):
        path = os.path.join(self.tmpdir, "usage.json")
        with open(path, "w", encoding="utf-8") as f:
            for i in range(flt.USAGE_LOG_TRIM_TRIGGER + 1):
                f.write(f'{{"n": {i}}}\n')
        flt.rotate_usage_log_if_needed(path)
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        self.assertEqual(len(lines), flt.USAGE_LOG_KEEP_LINES)
        self.assertEqual(json.loads(lines[-1])["n"], flt.USAGE_LOG_TRIM_TRIGGER)

    def test_under_trigger_left_untouched(self):
        path = os.path.join(self.tmpdir, "usage.json")
        with open(path, "w", encoding="utf-8") as f:
            for i in range(10):
                f.write(f'{{"n": {i}}}\n')
        flt.rotate_usage_log_if_needed(path)
        with open(path, "r", encoding="utf-8") as f:
            self.assertEqual(len(f.readlines()), 10)


class TestListTree(TempRepoTestCase):
    def test_lists_files_and_dirs(self):
        self.write("a.txt", "hi")
        self.write("sub/b.txt", "hi")
        entries = flt.list_tree(flt.ROOT)
        self.assertIn("a.txt", entries)
        self.assertIn("sub/", entries)
        self.assertIn("sub/b.txt", entries)

    def test_skips_excluded_dirs(self):
        self.write("node_modules/lib.js", "x")
        self.write("app/a.js", "x")
        entries = flt.list_tree(flt.ROOT)
        self.assertTrue(all("node_modules" not in e for e in entries))
        self.assertIn("app/a.js", entries)

    def test_empty_dir_returns_empty_list(self):
        os.makedirs(os.path.join(self.tmpdir, "empty"))
        self.assertEqual(flt.list_tree(os.path.join(self.tmpdir, "empty")), [])

    def test_non_directory_exits(self):
        self.write("a.txt", "hi")
        with self.assertRaises(SystemExit):
            flt.list_tree(os.path.join(self.tmpdir, "a.txt"))


class TestGitDiff(TempRepoTestCase):
    def _init_repo(self):
        subprocess.run(["git", "init", "-q"], cwd=self.tmpdir, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=self.tmpdir, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=self.tmpdir, check=True)
        self.write("a.txt", "line1\n")
        subprocess.run(["git", "add", "a.txt"], cwd=self.tmpdir, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=self.tmpdir, check=True)

    def test_not_a_git_repo_errors(self):
        with self.assertRaises(SystemExit):
            flt.get_git_diff(flt.ROOT)

    def test_no_changes_returns_empty_string(self):
        self._init_repo()
        self.assertEqual(flt.get_git_diff(flt.ROOT).strip(), "")

    def test_unstaged_change_shows_in_diff(self):
        self._init_repo()
        self.write("a.txt", "line1\nline2\n")
        diff = flt.get_git_diff(flt.ROOT)
        self.assertIn("line2", diff)

    def test_staged_change_shows_in_diff(self):
        self._init_repo()
        self.write("a.txt", "line1\nline2\n")
        subprocess.run(["git", "add", "a.txt"], cwd=self.tmpdir, check=True)
        diff = flt.get_git_diff(flt.ROOT)
        self.assertIn("line2", diff)

    def test_path_scopes_diff_to_single_file(self):
        self._init_repo()
        self.write("b.txt", "line1\n")
        subprocess.run(["git", "add", "b.txt"], cwd=self.tmpdir, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "add b"], cwd=self.tmpdir, check=True)
        self.write("a.txt", "line1\nline2\n")
        self.write("b.txt", "line1\nline2\n")
        diff = flt.get_git_diff(flt.ROOT, os.path.join(flt.ROOT, "a.txt"))
        self.assertIn("a.txt", diff)
        self.assertNotIn("b.txt", diff)


class TestResolveModel(unittest.TestCase):
    def test_explicit_model_available_returned(self):
        with mock.patch.object(flt, "list_models", return_value={"qwen2.5:7b", "gpt-oss:20b"}):
            self.assertEqual(
                flt.resolve_model("ollama", "http://x", "qwen2.5:7b"), "qwen2.5:7b"
            )

    def test_explicit_model_missing_exits(self):
        with mock.patch.object(flt, "list_models", return_value={"gpt-oss:20b"}):
            with self.assertRaises(SystemExit):
                flt.resolve_model("ollama", "http://x", "qwen2.5:7b")

    def test_no_model_picks_ollama_default_when_present(self):
        with mock.patch.object(flt, "list_models", return_value={"qwen2.5:7b", "gpt-oss:20b"}), \
             mock.patch.object(flt, "running_ollama_model", return_value=None):
            self.assertEqual(flt.resolve_model("ollama", "http://x", None), "qwen2.5:7b")

    def test_no_model_falls_back_to_sorted_first(self):
        with mock.patch.object(flt, "list_models", return_value={"zeta", "alpha"}), \
             mock.patch.object(flt, "running_ollama_model", return_value=None):
            self.assertEqual(flt.resolve_model("ollama", "http://x", None), "alpha")

    def test_no_model_prefers_currently_running_ollama_model(self):
        with mock.patch.object(flt, "list_models", return_value={"qwen2.5:7b", "qwen3:8b"}), \
             mock.patch.object(flt, "running_ollama_model", return_value="qwen3:8b"):
            self.assertEqual(flt.resolve_model("ollama", "http://x", None), "qwen3:8b")

    def test_running_model_not_in_pulled_list_ignored(self):
        with mock.patch.object(flt, "list_models", return_value={"qwen2.5:7b"}), \
             mock.patch.object(flt, "running_ollama_model", return_value="stale-unpulled-model"):
            self.assertEqual(flt.resolve_model("ollama", "http://x", None), "qwen2.5:7b")

    def test_lmstudio_no_default_falls_back_to_first_v1_entry_when_no_state_data(self):
        with mock.patch.object(flt, "list_models", return_value=["b-model", "a-model"]), \
             mock.patch.object(flt, "running_lmstudio_model", return_value=None):
            self.assertEqual(flt.resolve_model("lmstudio", "http://x", None), "b-model")

    def test_lmstudio_prefers_actually_loaded_model_over_v1_order(self):
        with mock.patch.object(flt, "list_models", return_value=["b-model", "a-model"]), \
             mock.patch.object(flt, "running_lmstudio_model", return_value="a-model"):
            self.assertEqual(flt.resolve_model("lmstudio", "http://x", None), "a-model")

    def test_openai_no_default_picks_first_reported(self):
        with mock.patch.object(flt, "list_models", return_value=["b-model", "a-model"]):
            self.assertEqual(flt.resolve_model("openai", "http://x", None), "b-model")

    def test_openai_explicit_model_missing_exits(self):
        with mock.patch.object(flt, "list_models", return_value={"other-model"}):
            with self.assertRaises(SystemExit):
                flt.resolve_model("openai", "http://x", "missing-model")

    def test_no_models_available_exits(self):
        with mock.patch.object(flt, "list_models", return_value=set()):
            with self.assertRaises(SystemExit):
                flt.resolve_model("ollama", "http://x", None)


class TestListModels(unittest.TestCase):
    def _fake_response(self, payload):
        body = json.dumps(payload).encode("utf-8")
        cm = mock.MagicMock()
        cm.__enter__.return_value = io.BytesIO(body)
        cm.__exit__.return_value = False
        return cm

    def test_ollama_parses_names(self):
        payload = {"models": [{"name": "qwen2.5:7b"}, {"name": "gpt-oss:20b"}]}
        with mock.patch("urllib.request.urlopen", return_value=self._fake_response(payload)):
            names = flt.list_models("ollama", "http://x")
        self.assertEqual(names, ["qwen2.5:7b", "gpt-oss:20b"])

    def test_lmstudio_parses_ids_preserving_order(self):
        payload = {"data": [{"id": "google/gemma-4-e2b"}, {"id": "qwen/qwen3.5-9b"}]}
        with mock.patch("urllib.request.urlopen", return_value=self._fake_response(payload)):
            names = flt.list_models("lmstudio", "http://x")
        self.assertEqual(names, ["google/gemma-4-e2b", "qwen/qwen3.5-9b"])

    def test_openai_parses_ids_same_shape_as_lmstudio(self):
        payload = {"data": [{"id": "llama-3-8b-instruct"}]}
        with mock.patch("urllib.request.urlopen", return_value=self._fake_response(payload)):
            names = flt.list_models("openai", "http://x")
        self.assertEqual(names, ["llama-3-8b-instruct"])

    def test_unreachable_exits_with_backend_specific_hint(self):
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            with self.assertRaises(SystemExit) as ctx:
                flt.list_models("ollama", "http://localhost:11434")
        self.assertIn("ollama serve", str(ctx.exception))

        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            with self.assertRaises(SystemExit) as ctx:
                flt.list_models("lmstudio", "http://localhost:1234")
        self.assertIn("LM Studio", str(ctx.exception))


class TestRunningOllamaModel(unittest.TestCase):
    def _fake_response(self, payload):
        body = json.dumps(payload).encode("utf-8")
        cm = mock.MagicMock()
        cm.__enter__.return_value = io.BytesIO(body)
        cm.__exit__.return_value = False
        return cm

    def test_returns_loaded_model_name(self):
        payload = {"models": [{"name": "qwen3:8b"}]}
        with mock.patch("urllib.request.urlopen", return_value=self._fake_response(payload)):
            self.assertEqual(flt.running_ollama_model("http://x"), "qwen3:8b")

    def test_no_model_loaded_returns_none(self):
        with mock.patch("urllib.request.urlopen", return_value=self._fake_response({"models": []})):
            self.assertIsNone(flt.running_ollama_model("http://x"))

    def test_unreachable_returns_none_not_exit(self):
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            self.assertIsNone(flt.running_ollama_model("http://x"))

        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            with self.assertRaises(SystemExit) as ctx:
                flt.list_models("openai", "http://localhost:8080")
        self.assertIn("openai not reachable", str(ctx.exception))


class TestRunningLmstudioModel(unittest.TestCase):
    def _fake_response(self, payload):
        body = json.dumps(payload).encode("utf-8")
        cm = mock.MagicMock()
        cm.__enter__.return_value = io.BytesIO(body)
        cm.__exit__.return_value = False
        return cm

    def test_returns_the_loaded_entry_ignoring_not_loaded_ones(self):
        payload = {"data": [
            {"id": "google/gemma-4-e4b", "state": "not-loaded"},
            {"id": "qwen/qwen3.5-9b", "state": "loaded"},
            {"id": "google/gemma-4-e2b", "state": "not-loaded"},
        ]}
        with mock.patch("urllib.request.urlopen", return_value=self._fake_response(payload)):
            self.assertEqual(flt.running_lmstudio_model("http://x"), "qwen/qwen3.5-9b")

    def test_nothing_loaded_returns_none(self):
        payload = {"data": [{"id": "google/gemma-4-e4b", "state": "not-loaded"}]}
        with mock.patch("urllib.request.urlopen", return_value=self._fake_response(payload)):
            self.assertIsNone(flt.running_lmstudio_model("http://x"))

    def test_unreachable_returns_none_not_exit(self):
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            self.assertIsNone(flt.running_lmstudio_model("http://x"))


class TestCallLLM(unittest.TestCase):
    def _fake_response(self, payload):
        body = json.dumps(payload).encode("utf-8")
        cm = mock.MagicMock()
        cm.__enter__.return_value = io.BytesIO(body)
        cm.__exit__.return_value = False
        return cm

    def test_ollama_returns_response_field(self):
        with mock.patch("urllib.request.urlopen", return_value=self._fake_response({"response": " hi "})):
            out = flt.call_llm("ollama", "http://x", "qwen2.5:7b", "task", "content", 100)
        self.assertEqual(out, "hi")

    def test_lmstudio_returns_message_content(self):
        payload = {"choices": [{"message": {"content": " hi "}}]}
        with mock.patch("urllib.request.urlopen", return_value=self._fake_response(payload)):
            out = flt.call_llm("lmstudio", "http://x", "some-model", "task", "content", 100)
        self.assertEqual(out, "hi")

    def test_openai_backend_uses_chat_completions_shape(self):
        captured = {}

        def fake_urlopen(req, timeout=120):
            captured["url"] = req.full_url
            captured["body"] = json.loads(req.data)
            payload = {"choices": [{"message": {"content": " filtered "}}]}
            return self._fake_response(payload)

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            out = flt.call_llm("openai", "http://localhost:8080", "llama-3-8b", "task", "content", 100)
        self.assertEqual(out, "filtered")
        self.assertEqual(captured["url"], "http://localhost:8080/v1/chat/completions")
        self.assertEqual(captured["body"]["model"], "llama-3-8b")
        self.assertEqual(captured["body"]["messages"][0]["role"], "system")

    def test_truncates_oversized_content(self):
        big = "x" * (flt.MAX_CONTENT_CHARS + 1000)
        captured = {}

        def fake_urlopen(req, timeout=120):
            captured["body"] = json.loads(req.data)
            return self._fake_response({"response": "ok"})

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            with mock.patch("sys.stderr", new_callable=io.StringIO):
                flt.call_llm("ollama", "http://x", "qwen2.5:7b", "task", big, 100)
        self.assertLessEqual(len(captured["body"]["prompt"]), flt.MAX_CONTENT_CHARS + 500)

    def test_http_error_surfaces_response_body_message(self):
        def raise_http_error(req, timeout=120):
            body = json.dumps({"error": {"message": "insufficient system resources"}}).encode()
            raise urllib.error.HTTPError("http://x", 400, "Bad Request", {}, io.BytesIO(body))

        with mock.patch("urllib.request.urlopen", side_effect=raise_http_error):
            with self.assertRaises(SystemExit) as ctx:
                flt.call_llm("lmstudio", "http://x", "m", "task", "content", 100)
        self.assertIn("insufficient system resources", str(ctx.exception))

    def test_connection_refused_gives_clear_error(self):
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            with self.assertRaises(SystemExit) as ctx:
                flt.call_llm("ollama", "http://x", "qwen2.5:7b", "task", "content", 100)
        self.assertIn("cannot reach ollama", str(ctx.exception))


class TestCallLLMChunked(unittest.TestCase):
    def _fake_response(self, payload):
        body = json.dumps(payload).encode("utf-8")
        cm = mock.MagicMock()
        cm.__enter__.return_value = io.BytesIO(body)
        cm.__exit__.return_value = False
        return cm

    def test_small_content_makes_single_call(self):
        calls = []

        def fake_urlopen(req, timeout=120):
            calls.append(json.loads(req.data))
            return self._fake_response({"response": "ok"})

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            out = flt.call_llm_chunked("ollama", "http://x", "qwen2.5:7b", "task", "small", 100)
        self.assertEqual(out, "ok")
        self.assertEqual(len(calls), 1)

    def test_oversized_content_splits_into_multiple_calls_and_joins_results(self):
        big = "x" * (flt.MAX_CONTENT_CHARS * 2 + 100)
        prompts = []

        def fake_urlopen(req, timeout=120):
            body = json.loads(req.data)
            prompts.append(body["prompt"])
            return self._fake_response({"response": f"chunk-result-{len(prompts)}"})

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            with mock.patch("sys.stderr", new_callable=io.StringIO):
                out = flt.call_llm_chunked("ollama", "http://x", "qwen2.5:7b", "task", big, 100)
        self.assertEqual(len(prompts), 3)
        self.assertIn("chunk-result-1", out)
        self.assertIn("chunk-result-3", out)

    def test_no_relevant_content_chunks_are_dropped(self):
        big = "x" * (flt.MAX_CONTENT_CHARS + 100)
        responses = iter(["NO_RELEVANT_CONTENT", "found it"])

        def fake_urlopen(req, timeout=120):
            return self._fake_response({"response": next(responses)})

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            with mock.patch("sys.stderr", new_callable=io.StringIO):
                out = flt.call_llm_chunked("ollama", "http://x", "qwen2.5:7b", "task", big, 100)
        self.assertEqual(out, "found it")

    def test_all_chunks_irrelevant_returns_sentinel(self):
        big = "x" * (flt.MAX_CONTENT_CHARS + 100)

        def fake_urlopen(req, timeout=120):
            return self._fake_response({"response": "NO_RELEVANT_CONTENT"})

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            with mock.patch("sys.stderr", new_callable=io.StringIO):
                out = flt.call_llm_chunked("ollama", "http://x", "qwen2.5:7b", "task", big, 100)
        self.assertEqual(out, "NO_RELEVANT_CONTENT")


class TestCLIArgGating(unittest.TestCase):
    SCRIPT = os.path.join(os.path.dirname(os.path.realpath(__file__)), "filter.py")

    def test_openai_backend_without_host_errors(self):
        result = subprocess.run(
            [sys.executable, self.SCRIPT, "--backend", "openai", "--task", "x", "--input", self.SCRIPT],
            capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--host is required for --backend openai", result.stderr)

    def test_openai_backend_with_host_and_no_server_fails_late(self):
        result = subprocess.run(
            [sys.executable, self.SCRIPT, "--backend", "openai", "--host", "http://localhost:59999",
             "--task", "x", "--input", self.SCRIPT],
            capture_output=True, text=True, timeout=10,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("openai not reachable", result.stdout + result.stderr)

    def test_diff_and_grep_mutually_exclusive(self):
        result = subprocess.run(
            [sys.executable, self.SCRIPT, "--diff", "--grep", "x"],
            capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("mutually exclusive", result.stderr)

    def test_ls_and_grep_mutually_exclusive(self):
        result = subprocess.run(
            [sys.executable, self.SCRIPT, "--ls", "--grep", "x"],
            capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("mutually exclusive", result.stderr)

    def test_ls_and_diff_mutually_exclusive(self):
        result = subprocess.run(
            [sys.executable, self.SCRIPT, "--ls", "--diff"],
            capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("mutually exclusive", result.stderr)

    def test_find_and_grep_mutually_exclusive(self):
        result = subprocess.run(
            [sys.executable, self.SCRIPT, "--find", "*.js", "--grep", "x"],
            capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("mutually exclusive", result.stderr)

    def test_report_and_grep_mutually_exclusive(self):
        result = subprocess.run(
            [sys.executable, self.SCRIPT, "--report", "--grep", "x"],
            capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--report cannot be combined", result.stderr)

    def test_clean_and_ls_mutually_exclusive(self):
        result = subprocess.run(
            [sys.executable, self.SCRIPT, "--clean", "--ls"],
            capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--clean cannot be combined", result.stderr)

    def test_clean_and_report_mutually_exclusive(self):
        result = subprocess.run(
            [sys.executable, self.SCRIPT, "--clean", "--report"],
            capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--clean cannot be combined", result.stderr)


class TestReadInput(TempRepoTestCase):
    def test_reads_from_file(self):
        self.write("a.txt", "hello")
        self.assertEqual(flt.read_input("a.txt"), "hello")

    def test_reads_from_stdin_when_no_input(self):
        with mock.patch("sys.stdin") as fake_stdin:
            fake_stdin.isatty.return_value = False
            fake_stdin.read.return_value = "piped content"
            self.assertEqual(flt.read_input(None), "piped content")

    def test_no_input_and_no_stdin_exits(self):
        with mock.patch("sys.stdin") as fake_stdin:
            fake_stdin.isatty.return_value = True
            with self.assertRaises(SystemExit):
                flt.read_input(None)


class TestCLIEndToEnd(TempRepoTestCase):
    """Runs the real CLI as a subprocess — no network needed for --grep/--diff without --task."""

    SCRIPT = os.path.join(os.path.dirname(os.path.realpath(__file__)), "filter.py")

    def run_cli(self, *args):
        env = {**os.environ, "LOCAL_CONTEXT_FILTER_LOG": os.path.join(self.tmpdir, "usage.json")}
        return subprocess.run(
            [sys.executable, self.SCRIPT, *args], cwd=self.tmpdir, capture_output=True, text=True, env=env,
        )

    def test_grep_prints_matches(self):
        self.write("a.js", "const ServiceOrder = 1;\n")
        result = self.run_cli("--grep", "ServiceOrder")
        self.assertEqual(result.returncode, 0)
        self.assertIn("a.js:1:", result.stdout)

    def test_grep_input_single_file_is_searched(self):
        self.write("sub/a.js", "const ServiceOrder = 1;\n")
        self.write("sub/b.js", "const ServiceOrder = 2;\n")
        result = self.run_cli("--grep", "ServiceOrder", "--input", "sub/a.js")
        self.assertEqual(result.returncode, 0)
        self.assertIn("a.js:1:", result.stdout)
        self.assertNotIn("b.js", result.stdout)

    def test_grep_no_matches_prints_sentinel(self):
        self.write("a.js", "nothing here\n")
        result = self.run_cli("--grep", "ServiceOrder")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "NO_MATCHES")

    def test_grep_ignore_case_flag(self):
        self.write("a.js", "SERVICEORDER\n")
        result = self.run_cli("--grep", "serviceorder", "--ignore-case")
        self.assertIn("a.js:1:", result.stdout)

    def _init_repo(self):
        subprocess.run(["git", "init", "-q"], cwd=self.tmpdir, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=self.tmpdir, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=self.tmpdir, check=True)
        self.write("a.txt", "line1\n")
        subprocess.run(["git", "add", "a.txt"], cwd=self.tmpdir, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=self.tmpdir, check=True)

    def test_diff_prints_raw_diff(self):
        self._init_repo()
        self.write("a.txt", "line1\nline2\n")
        result = self.run_cli("--diff")
        self.assertEqual(result.returncode, 0)
        self.assertIn("+line2", result.stdout)

    def test_diff_no_changes_prints_sentinel(self):
        self._init_repo()
        result = self.run_cli("--diff")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "NO_CHANGES")

    def test_diff_scoped_to_input(self):
        self._init_repo()
        self.write("b.txt", "line1\n")
        subprocess.run(["git", "add", "b.txt"], cwd=self.tmpdir, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "add b"], cwd=self.tmpdir, check=True)
        self.write("a.txt", "line1\nline2\n")
        self.write("b.txt", "line1\nline2\n")
        result = self.run_cli("--diff", "--input", "a.txt")
        self.assertIn("a.txt", result.stdout)
        self.assertNotIn("b.txt", result.stdout)

    def test_diff_not_a_git_repo_errors(self):
        result = self.run_cli("--diff")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("error:", result.stdout + result.stderr)

    def test_ls_prints_entries(self):
        self.write("a.txt", "hi")
        self.write("sub/b.txt", "hi")
        result = self.run_cli("--ls")
        self.assertEqual(result.returncode, 0)
        self.assertIn("a.txt", result.stdout)
        self.assertIn("sub/", result.stdout)

    def test_ls_empty_dir_prints_sentinel(self):
        result = self.run_cli("--ls")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "NO_ENTRIES")

    def test_ls_scoped_to_input(self):
        self.write("a.txt", "hi")
        self.write("sub/b.txt", "hi")
        result = self.run_cli("--ls", "--input", "sub")
        self.assertEqual(result.returncode, 0)
        self.assertIn("b.txt", result.stdout)
        self.assertNotIn("a.txt", result.stdout)

    def test_find_prints_matches(self):
        self.write("src/app.js", "x")
        self.write("README.md", "x")
        result = self.run_cli("--find", "*.js")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "src/app.js")

    def test_find_no_matches_prints_sentinel(self):
        self.write("README.md", "x")
        result = self.run_cli("--find", "*.js")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "NO_MATCHES")

    def test_find_respects_project_excludes_config(self):
        self.write("fixtures/dump.js", "x")
        self.write("src/app.js", "x")
        self.write(".claude/local-context-filter.json", json.dumps({"exclude": ["fixtures"]}))
        result = self.run_cli("--find", "*.js")
        self.assertEqual(result.stdout.strip(), "src/app.js")

    def test_report_no_data_prints_sentinel(self):
        result = self.run_cli("--report")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "NO_USAGE_DATA")

    def test_report_after_usage_shows_totals(self):
        self.write("a.js", "const ServiceOrder = 1;\n")
        self.run_cli("--grep", "ServiceOrder")
        result = self.run_cli("--report")
        self.assertEqual(result.returncode, 0)
        self.assertIn("1 runs", result.stdout)
        self.assertIn("grep", result.stdout)

    def test_clean_removes_log_and_is_idempotent(self):
        self.write("a.js", "const ServiceOrder = 1;\n")
        self.run_cli("--grep", "ServiceOrder")
        log_path = os.path.join(self.tmpdir, "usage.json")
        self.assertTrue(os.path.exists(log_path))
        result = self.run_cli("--clean")
        self.assertEqual(result.stdout.strip(), "USAGE_LOG_CLEARED")
        self.assertFalse(os.path.exists(log_path))
        result = self.run_cli("--clean")
        self.assertEqual(result.stdout.strip(), "NO_USAGE_DATA")

    def test_count_single_file(self):
        self.write("a.txt", "l1\nl2\nl3\n")
        result = self.run_cli("--count", "--input", "a.txt")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "3 a.txt")

    def test_count_directory_includes_total(self):
        self.write("a.txt", "l1\nl2\n")
        self.write("sub/b.txt", "l1\n")
        result = self.run_cli("--count")
        self.assertEqual(result.returncode, 0)
        self.assertIn("2 a.txt", result.stdout)
        self.assertIn("1 sub/b.txt", result.stdout)
        self.assertIn("3 TOTAL", result.stdout)

    def test_count_empty_dir_prints_sentinel(self):
        result = self.run_cli("--count")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "NO_ENTRIES")

    def test_run_rejects_non_allowed_binary(self):
        result = self.run_cli("--run", "rm -rf /")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("only allows npm/npx/pnpm/yarn", result.stdout + result.stderr)

    def test_run_npm_version_prints_output(self):
        result = self.run_cli("--run", "npm --version")
        self.assertEqual(result.returncode, 0)
        self.assertRegex(result.stdout.strip(), r"^\d+\.\d+\.\d+$")

    def test_run_and_grep_mutually_exclusive(self):
        result = self.run_cli("--run", "npm --version", "--grep", "x")
        self.assertNotEqual(result.returncode, 0)

    def test_count_and_diff_mutually_exclusive(self):
        result = self.run_cli("--count", "--diff")
        self.assertNotEqual(result.returncode, 0)


class TestReadDirectory(TempRepoTestCase):
    def test_reads_and_tags_files(self):
        self.write("a.txt", "hello")
        self.write("sub/b.txt", "world")
        content = flt.read_directory(flt.ROOT)
        self.assertIn("--- a.txt ---\nhello", content)
        self.assertIn("world", content)

    def test_empty_dir_exits(self):
        os.makedirs(os.path.join(self.tmpdir, "empty"))
        with self.assertRaises(SystemExit):
            flt.read_directory(os.path.join(self.tmpdir, "empty"))


if __name__ == "__main__":
    unittest.main()
