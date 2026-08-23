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

    def tearDown(self):
        os.chdir(self._orig_cwd)
        flt.ROOT = self._orig_root
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

    def test_no_matches_returns_empty_list(self):
        self.write("a.js", "nothing here")
        self.assertEqual(flt.grep_search(flt.ROOT, "ServiceOrder"), [])

    def test_binary_file_skipped_without_crash(self):
        with open(os.path.join(self.tmpdir, "bin.dat"), "wb") as f:
            f.write(b"\xff\xfe\x00\x01ServiceOrder")
        matches = flt.grep_search(flt.ROOT, "ServiceOrder")
        self.assertEqual(matches, [])


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
        with mock.patch.object(flt, "list_models", return_value={"qwen2.5:7b", "gpt-oss:20b"}):
            self.assertEqual(flt.resolve_model("ollama", "http://x", None), "qwen2.5:7b")

    def test_no_model_falls_back_to_sorted_first(self):
        with mock.patch.object(flt, "list_models", return_value={"zeta", "alpha"}):
            self.assertEqual(flt.resolve_model("ollama", "http://x", None), "alpha")

    def test_lmstudio_no_default_picks_sorted_first(self):
        with mock.patch.object(flt, "list_models", return_value={"b-model", "a-model"}):
            self.assertEqual(flt.resolve_model("lmstudio", "http://x", None), "a-model")

    def test_openai_no_default_picks_sorted_first(self):
        with mock.patch.object(flt, "list_models", return_value={"b-model", "a-model"}):
            self.assertEqual(flt.resolve_model("openai", "http://x", None), "a-model")

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
        self.assertEqual(names, {"qwen2.5:7b", "gpt-oss:20b"})

    def test_lmstudio_parses_ids(self):
        payload = {"data": [{"id": "google/gemma-4-e2b"}, {"id": "qwen/qwen3.5-9b"}]}
        with mock.patch("urllib.request.urlopen", return_value=self._fake_response(payload)):
            names = flt.list_models("lmstudio", "http://x")
        self.assertEqual(names, {"google/gemma-4-e2b", "qwen/qwen3.5-9b"})

    def test_openai_parses_ids_same_shape_as_lmstudio(self):
        payload = {"data": [{"id": "llama-3-8b-instruct"}]}
        with mock.patch("urllib.request.urlopen", return_value=self._fake_response(payload)):
            names = flt.list_models("openai", "http://x")
        self.assertEqual(names, {"llama-3-8b-instruct"})

    def test_unreachable_exits_with_backend_specific_hint(self):
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            with self.assertRaises(SystemExit) as ctx:
                flt.list_models("ollama", "http://localhost:11434")
        self.assertIn("ollama serve", str(ctx.exception))

        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            with self.assertRaises(SystemExit) as ctx:
                flt.list_models("lmstudio", "http://localhost:1234")
        self.assertIn("LM Studio", str(ctx.exception))

        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            with self.assertRaises(SystemExit) as ctx:
                flt.list_models("openai", "http://localhost:8080")
        self.assertIn("openai not reachable", str(ctx.exception))


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
        self.assertIn("--diff cannot be combined", result.stderr)


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
