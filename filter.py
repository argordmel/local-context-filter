#!/usr/bin/env python3
"""Filter/summarize raw content via a local LLM, keeping only what's relevant to a task.

Usage:
  python3 filter.py --task "TASK" --input FILE_OR_DIR [--backend ollama|lmstudio|openai] [--host URL] [--model TAG] [--max-words N]
  cat file | python3 filter.py --task "TASK"
  python3 filter.py --grep PATTERN [--input DIR] [--task "TASK"]
  python3 filter.py --diff [--input PATH] [--task "TASK"]
  python3 filter.py --ls [--input DIR] [--task "TASK"]
  python3 filter.py --find PATTERN [--input DIR] [--task "TASK"]
  python3 filter.py --run "npm install" [--input DIR] [--task "TASK"]
  python3 filter.py --count [--input FILE_OR_DIR] [--task "TASK"]
  python3 filter.py --report

--input may be a file or a directory (searched recursively, subdirs included).
Paths are confined to the current working directory: parent traversal (../,
paths outside cwd) is rejected.

--grep does a real recursive regex search (like `grep -rn`) under --input
(default: cwd) and prints exact "path:line: content" matches — no LLM
involved, cheapest and most precise option for exact-pattern search. Add
--task on top of --grep to additionally have the local model narrow a large
match list down to what's relevant to that task.

--diff runs `git diff HEAD` (staged + unstaged) at cwd, or scoped to
--input (a single file or directory) if given, and prints it as-is — no
LLM involved. Add --task to have the local model filter it down to what's
relevant to that task instead.

--ls recursively lists files and directories under --input (default: cwd),
skipping the same excluded dirs as --grep — no LLM, no file contents read.
Add --task to have the local model narrow a large listing down to what's
relevant.

--find matches file/dir basenames against a glob pattern (like `find
-iname`, e.g. "*.log") under --input, skipping the same excluded dirs — no
LLM, no file contents read. Add --task to have the local model narrow a
large match list down to what's relevant.

--run runs an npm/npx/pnpm/yarn command (only these four binaries are
allowed) at --input (default: cwd) and prints combined stdout+stderr — no
LLM involved. Add --task to have the local model filter noisy install
output down to what's relevant (e.g. actual errors). --diff, --grep, --ls,
--find, and --run are all mutually exclusive with each other.

--count counts lines per file (like `wc -l`) under --input (default: cwd),
skipping the same excluded dirs as --grep/--ls/--find — no LLM, no file
content in the result, just "N path" per file plus a "N TOTAL" line. Add
--task to have the local model narrow a large listing down to what's
relevant.

--report prints a summary of usage.json (total runs and estimated tokens
saved, broken down by mode) — no LLM, cannot combine with anything else.

--clean deletes usage.json — no LLM, cannot combine with anything else.

--backend selects the local server: "ollama" (default, http://localhost:11434),
"lmstudio" (OpenAI-compatible, http://localhost:1234), or "openai" (any other
OpenAI-compatible server — llama.cpp server, vLLM, etc.) which requires
--host since there's no conventional default port. Override ollama/lmstudio's
default host with --host too.

Prints the filtered content to stdout. Nothing else goes to stdout.

Every run appends one line to usage.json (next to this script, gitignored):
a local-only, no-network JSON record of {mode, backend, chars_in, chars_out,
tokens_saved_est} — no file contents, paths, or task text. Purely so you can
eyeball how much this has saved over time; never blocks the command if the
log can't be written.
"""
import argparse
import fnmatch
import json
import os
import re
import shlex
import subprocess
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone

ROOT = os.path.realpath(os.getcwd())
EXCLUDED_DIRS = {".git", "node_modules", "dist", "build", ".venv", "__pycache__", ".next", "coverage"}
MAX_GREP_MATCHES = 500
ALLOWED_RUN_BINS = {"npm", "npx", "pnpm", "yarn"}
RUN_TIMEOUT = 600
LOG_PATH = os.environ.get(
    "LOCAL_CONTEXT_FILTER_LOG",
    os.path.join(os.path.dirname(os.path.realpath(__file__)), "usage.json"),
)

DEFAULT_HOSTS = {
    "ollama": "http://localhost:11434",
    "lmstudio": "http://localhost:1234",
}
DEFAULT_MODELS = {
    "ollama": "qwen2.5:7b",
}

SYSTEM_PROMPT = (
    "You are a context filter. You receive a TASK and RAW_CONTENT. "
    "Output ONLY the information from RAW_CONTENT that is relevant to TASK. "
    "Be concise and factual. Preserve exact code, numbers, names, and errors verbatim. "
    "Discard anything irrelevant to TASK. Do not add commentary, opinions, or explain what you did. "
    "If nothing is relevant, output exactly: NO_RELEVANT_CONTENT."
)


def list_models(backend, host):
    """Return the set of model names/ids currently available on the backend."""
    url = f"{host}/api/tags" if backend == "ollama" else f"{host}/v1/models"
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            data = json.loads(resp.read())
    except urllib.error.URLError:
        if backend == "ollama":
            start_hint = "`ollama serve`"
        elif backend == "lmstudio":
            start_hint = "LM Studio's local server (Developer tab > Start Server)"
        else:
            start_hint = "the server"
        sys.exit(f"error: {backend} not reachable at {host}. Start it with {start_hint}.")
    if backend == "ollama":
        return {m.get("name") for m in data.get("models", [])}
    return {m.get("id") for m in data.get("data", [])}


def running_ollama_model(host):
    """Return the name of the model currently loaded in Ollama's memory (via
    /api/ps), or None if none is loaded or the check fails for any reason.

    Never raises — this is a best-effort convenience, not a requirement.
    """
    try:
        with urllib.request.urlopen(f"{host}/api/ps", timeout=3) as resp:
            data = json.loads(resp.read())
    except (urllib.error.URLError, json.JSONDecodeError):
        return None
    models = data.get("models", [])
    return models[0].get("name") if models else None


def resolve_model(backend, host, model):
    """Validate an explicit --model, or auto-pick one when none was given.

    Auto-pick order for ollama: the model currently loaded in memory (so it
    matches whatever you're already running, no extra load time) > the
    hardcoded default if pulled > alphabetically first pulled model.
    """
    names = list_models(backend, host)
    if model:
        if model not in names:
            if backend == "ollama":
                fix = f"`ollama pull {model}`"
            elif backend == "lmstudio":
                fix = "load it in LM Studio first"
            else:
                fix = "load it on the server first"
            sys.exit(f"error: model '{model}' not available on {backend}. {fix} (available: {', '.join(sorted(names)) or 'none'}).")
        return model
    if backend == "ollama":
        running = running_ollama_model(host)
        if running and running in names:
            return running
    if backend in DEFAULT_MODELS and DEFAULT_MODELS[backend] in names:
        return DEFAULT_MODELS[backend]
    if names:
        return sorted(names)[0]
    sys.exit(f"error: no models available on {backend} at {host}.")


def confine_to_root(path):
    """Resolve path and reject anything outside the current working directory (ROOT).

    Subdirectories are fine; parent traversal (../, absolute paths outside ROOT,
    symlinks pointing out) is rejected.
    """
    real = os.path.realpath(path)
    if real != ROOT and not real.startswith(ROOT + os.sep):
        sys.exit(f"error: '{path}' resolves outside the current directory ({ROOT}); refusing")
    return real


def read_input(path):
    if path:
        real = confine_to_root(path)
        if os.path.isdir(real):
            return read_directory(real)
        with open(real, "r", encoding="utf-8") as f:
            return f.read()
    if sys.stdin.isatty():
        sys.exit("error: no --input file and no stdin piped")
    return sys.stdin.read()


def load_project_excludes(root):
    """Read `.claude/local-context-filter.json` at root (if present) and return
    its "exclude" list as a set.

    Format: {"exclude": ["fixtures", "vendor"]} — extra directory names to skip
    on top of EXCLUDED_DIRS, for this project only. Missing/invalid file is
    silently treated as no extra excludes.
    """
    path = os.path.join(root, ".claude", "local-context-filter.json")
    if not os.path.isfile(path):
        return set()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return set()
    exclude = data.get("exclude", [])
    if not isinstance(exclude, list):
        return set()
    return {str(x) for x in exclude}


def grep_search(root, pattern, ignore_case=False, stats=None, excluded_dirs=None):
    """Recursively grep for `pattern` (regex) under root, skipping excluded_dirs (default: EXCLUDED_DIRS) and binary files.

    If `stats` (a dict) is passed, sets stats['chars_scanned'] to the total
    characters read across all scanned lines, matched or not — used only for
    local usage logging, doesn't affect the return value.
    """
    excluded = EXCLUDED_DIRS if excluded_dirs is None else excluded_dirs
    flags = re.IGNORECASE if ignore_case else 0
    rx = re.compile(pattern, flags)
    matches = []
    truncated = False
    chars_scanned = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in excluded]
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root)
            try:
                with open(full, "r", encoding="utf-8") as f:
                    for lineno, line in enumerate(f, start=1):
                        chars_scanned += len(line)
                        if rx.search(line):
                            matches.append(f"{rel}:{lineno}: {line.rstrip()}")
                            if len(matches) >= MAX_GREP_MATCHES:
                                truncated = True
                                break
            except (UnicodeDecodeError, OSError):
                continue
            if truncated:
                break
        if truncated:
            break
    if truncated:
        print(f"warning: hit {MAX_GREP_MATCHES}-match cap, results truncated; narrow the pattern or --input path", file=sys.stderr)
    if stats is not None:
        stats["chars_scanned"] = chars_scanned
    return matches


def find_search(root, pattern, ignore_case=False, excluded_dirs=None):
    """Find files/dirs whose basename matches a glob `pattern` (like `find -iname`),
    skipping excluded_dirs (default: EXCLUDED_DIRS). Directories are suffixed
    with '/'. No LLM, no file contents read — only names are compared.
    """
    excluded = EXCLUDED_DIRS if excluded_dirs is None else excluded_dirs
    check_pattern = pattern.lower() if ignore_case else pattern
    matches = []
    truncated = False
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in excluded)
        candidates = [(d, True) for d in dirnames] + [(f, False) for f in filenames]
        for name, is_dir in sorted(candidates):
            check_name = name.lower() if ignore_case else name
            if fnmatch.fnmatch(check_name, check_pattern):
                rel = os.path.relpath(os.path.join(dirpath, name), root)
                matches.append(rel + ("/" if is_dir else ""))
                if len(matches) >= MAX_GREP_MATCHES:
                    truncated = True
                    break
        if truncated:
            break
    if truncated:
        print(f"warning: hit {MAX_GREP_MATCHES}-match cap, results truncated; narrow the pattern or --input path", file=sys.stderr)
    return sorted(matches)


def generate_report(log_path):
    """Read LOG_PATH's JSON lines and return a plain-text usage summary.

    Returns the sentinel NO_USAGE_DATA if the log is missing or empty.
    """
    if not os.path.exists(log_path):
        return "NO_USAGE_DATA"
    entries = []
    with open(log_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if not entries:
        return "NO_USAGE_DATA"

    total_saved = sum(e.get("tokens_saved_est") or 0 for e in entries)
    by_mode = {}
    for e in entries:
        mode = e.get("mode", "unknown")
        agg = by_mode.setdefault(mode, {"runs": 0, "tokens_saved_est": 0})
        agg["runs"] += 1
        agg["tokens_saved_est"] += e.get("tokens_saved_est") or 0

    lines = [
        f"Usage report ({len(entries)} runs, {entries[0].get('ts', '?')} to {entries[-1].get('ts', '?')})",
        "",
        f"Total tokens saved (est.): {total_saved:,}",
        "",
        "By mode:",
    ]
    for mode in sorted(by_mode, key=lambda m: -by_mode[m]["tokens_saved_est"]):
        agg = by_mode[mode]
        lines.append(f"  {mode:<6} {agg['runs']:>4} runs   {agg['tokens_saved_est']:>10,} tokens saved (est.)")
    return "\n".join(lines)


def log_usage(mode, backend, chars_in, chars_out):
    """Append one JSON line to LOG_PATH with local-only usage metadata.

    No file contents, paths, or task text are recorded — only counts. Never
    raises: a logging failure (e.g. read-only filesystem) must not break the
    actual command.
    """
    try:
        tokens_saved_est = None
        if chars_in is not None and chars_out is not None:
            tokens_saved_est = max(0, chars_in - chars_out) // 4
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "mode": mode,
            "backend": backend,
            "chars_in": chars_in,
            "chars_out": chars_out,
            "tokens_saved_est": tokens_saved_est,
        }
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def count_lines(root, excluded_dirs=None):
    """Count lines per file, like `wc -l`, no LLM, no file content in the result.

    If root is a single file, returns one "N path" line. If root is a
    directory, recurses (skipping excluded_dirs) and appends a "TOTAL N"
    line, like `wc -l` on multiple files. Binary/unreadable files are
    skipped silently.
    """
    excluded = EXCLUDED_DIRS if excluded_dirs is None else excluded_dirs

    def line_count(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return sum(1 for _ in f)
        except (UnicodeDecodeError, OSError):
            return None

    if os.path.isfile(root):
        n = line_count(root)
        if n is None:
            sys.exit(f"error: '{root}' is not a readable text file")
        return f"{n} {os.path.basename(root)}"

    lines = []
    total = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in excluded)
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            n = line_count(full)
            if n is None:
                continue
            rel = os.path.relpath(full, root)
            lines.append(f"{n} {rel}")
            total += n
    if not lines:
        return None
    lines.append(f"{total} TOTAL")
    return "\n".join(lines)


def list_tree(root, excluded_dirs=None):
    """Recursively list files and directories under root, skipping excluded_dirs
    (default: EXCLUDED_DIRS).

    Directories are suffixed with '/'. No LLM involved, no file contents read.
    """
    excluded = EXCLUDED_DIRS if excluded_dirs is None else excluded_dirs
    if not os.path.isdir(root):
        sys.exit(f"error: '{root}' is not a directory")
    entries = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in excluded)
        rel_dir = os.path.relpath(dirpath, root)
        if rel_dir != ".":
            entries.append(rel_dir + "/")
        for name in sorted(filenames):
            entries.append(os.path.relpath(os.path.join(dirpath, name), root))
    return sorted(entries)


def read_directory(root):
    """Recursively read all files under root (subdirs included), each tagged by relative path."""
    chunks = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root)
            try:
                with open(full, "r", encoding="utf-8") as f:
                    chunks.append(f"--- {rel} ---\n{f.read()}")
            except (UnicodeDecodeError, OSError):
                continue
    if not chunks:
        sys.exit(f"error: no readable text files under '{root}'")
    return "\n\n".join(chunks)


def get_git_diff(root, path=None):
    """Return `git diff HEAD` (staged + unstaged) at root, as text.

    If path is given, scopes the diff to that file/directory (must already
    be confined to root by the caller).
    """
    cmd = ["git", "diff", "HEAD"]
    if path:
        cmd += ["--", os.path.relpath(path, root)]
    try:
        result = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        sys.exit("error: git not found on PATH")
    if result.returncode != 0:
        sys.exit(f"error: git diff failed: {result.stderr.strip()}")
    return result.stdout


def run_package_command(root, command):
    """Run an npm/npx/pnpm/yarn command at root, returning combined stdout+stderr.

    Only npm/npx/pnpm/yarn are allowed as the binary — this is a filter tool,
    not a general command runner, so anything else is rejected before exec.
    Does not use shell=True; the command is tokenized with shlex.
    """
    try:
        argv = shlex.split(command)
    except ValueError as e:
        sys.exit(f"error: could not parse --run command: {e}")
    if not argv:
        sys.exit("error: --run command is empty")
    bin_name = os.path.basename(argv[0])
    if bin_name not in ALLOWED_RUN_BINS:
        sys.exit(f"error: --run only allows npm/npx/pnpm/yarn commands, got '{bin_name}'")
    try:
        result = subprocess.run(
            argv, cwd=root, capture_output=True, text=True, timeout=RUN_TIMEOUT,
        )
    except FileNotFoundError:
        sys.exit(f"error: '{bin_name}' not found on PATH")
    except subprocess.TimeoutExpired:
        sys.exit(f"error: --run command timed out after {RUN_TIMEOUT}s")
    output = result.stdout + result.stderr
    if result.returncode != 0:
        output += f"\n[exit code {result.returncode}]"
    return output


MAX_CONTENT_CHARS = 24000  # ~6k tokens; keeps prompt+content under num_ctx below with room for output
NUM_CTX = 8192


def call_llm(backend, host, model, task, content, max_words):
    if len(content) > MAX_CONTENT_CHARS:
        print(
            f"warning: input is {len(content)} chars, truncating to {MAX_CONTENT_CHARS} "
            f"to fit the model's context window; split large directories into smaller runs",
            file=sys.stderr,
        )
        content = content[:MAX_CONTENT_CHARS]

    if backend == "ollama":
        prompt = (
            f"{SYSTEM_PROMPT}\n\n"
            f"TASK: {task}\n\n"
            f"Keep the output under {max_words} words.\n\n"
            f"RAW_CONTENT:\n{content}"
        )
        url = f"{host}/api/generate"
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"num_ctx": NUM_CTX, "temperature": 0.2},
        }
    else:  # lmstudio / openai: OpenAI-compatible chat completions
        url = f"{host}/v1/chat/completions"
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"TASK: {task}\n\nKeep the output under {max_words} words.\n\nRAW_CONTENT:\n{content}"
                    ),
                },
            ],
            "temperature": 0.2,
            "stream": False,
        }

    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(body).get("error", {}).get("message", body)
        except (json.JSONDecodeError, AttributeError):
            pass
        sys.exit(f"error: {backend} at {url} returned {e.code}: {body}")
    except urllib.error.URLError as e:
        sys.exit(f"error: cannot reach {backend} at {url} ({e}).")

    if backend == "ollama":
        return data.get("response", "").strip()
    return data["choices"][0]["message"]["content"].strip()


def call_llm_chunked(backend, host, model, task, content, max_words):
    """Like call_llm, but content over MAX_CONTENT_CHARS is split into sequential
    chunks and each is filtered separately instead of silently truncated — full
    coverage at the cost of one extra local LLM call per chunk (still free).
    """
    if len(content) <= MAX_CONTENT_CHARS:
        return call_llm(backend, host, model, task, content, max_words)
    chunks = [content[i:i + MAX_CONTENT_CHARS] for i in range(0, len(content), MAX_CONTENT_CHARS)]
    print(
        f"warning: input is {len(content)} chars, split into {len(chunks)} chunks of up to "
        f"{MAX_CONTENT_CHARS} chars each and filtered separately (slower, but nothing is dropped)",
        file=sys.stderr,
    )
    results = []
    for chunk in chunks:
        result = call_llm(backend, host, model, task, chunk, max_words)
        if result and result != "NO_RELEVANT_CONTENT":
            results.append(result)
    return "\n\n".join(results) if results else "NO_RELEVANT_CONTENT"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--task", help="what the filtered content will be used for (required unless --grep is used alone)")
    ap.add_argument("--input", help="file or directory to filter/search (recursive); with --diff, scopes the diff to this path; omit to read stdin (LLM mode) or diff the whole repo (--diff)")
    ap.add_argument("--grep", metavar="PATTERN", help="exact regex search (like grep -rn) under --input, no LLM")
    ap.add_argument("--ignore-case", action="store_true", help="case-insensitive --grep")
    ap.add_argument("--diff", action="store_true", help="filter `git diff HEAD` at cwd (or --input path); no LLM without --task")
    ap.add_argument("--ls", action="store_true", help="recursively list files/dirs under --input (default: cwd), no LLM without --task")
    ap.add_argument("--find", metavar="PATTERN", help="find files/dirs by basename glob (like `find -iname`) under --input, no LLM")
    ap.add_argument("--run", metavar="COMMAND", help="run an npm/npx/pnpm/yarn command (e.g. 'npm install') at --input (default: cwd) and print its output; no LLM without --task")
    ap.add_argument("--count", action="store_true", help="count lines per file under --input (default: cwd), like `wc -l`; no LLM without --task")
    ap.add_argument("--report", action="store_true", help="print a summary of usage.json (local totals); cannot combine with anything else")
    ap.add_argument("--clean", action="store_true", help="delete usage.json; cannot combine with anything else")
    ap.add_argument("--backend", choices=["ollama", "lmstudio", "openai"], default="ollama", help="local LLM server (default: ollama)")
    ap.add_argument("--host", help="override backend host (default: ollama=http://localhost:11434, lmstudio=http://localhost:1234; required for --backend openai)")
    ap.add_argument("--model", help="model tag/id; default: qwen2.5:7b on ollama, first available model on lmstudio/openai")
    ap.add_argument("--max-words", type=int, default=300, help="target max words of output (default: 300)")
    args = ap.parse_args()

    other_modes = args.diff or bool(args.grep) or args.ls or bool(args.find) or bool(args.run) or args.count or bool(args.task)
    if args.report and other_modes:
        ap.error("--report cannot be combined with --diff/--grep/--ls/--find/--run/--count/--task")
    if args.clean and (other_modes or args.report):
        ap.error("--clean cannot be combined with anything else")
    if args.report:
        print(generate_report(LOG_PATH))
        return
    if args.clean:
        existed = os.path.exists(LOG_PATH)
        if existed:
            try:
                os.remove(LOG_PATH)
            except OSError as e:
                sys.exit(f"error: could not remove usage log: {e}")
        print("USAGE_LOG_CLEARED" if existed else "NO_USAGE_DATA")
        return

    if sum([args.diff, bool(args.grep), args.ls, bool(args.find), bool(args.run), args.count]) > 1:
        ap.error("--diff, --grep, --ls, --find, --run, and --count are mutually exclusive")
    if args.backend == "openai" and not args.host:
        ap.error("--host is required for --backend openai (no conventional default port)")
    host = args.host or DEFAULT_HOSTS[args.backend]
    excluded_dirs = EXCLUDED_DIRS | load_project_excludes(ROOT)

    if args.ls:
        root = confine_to_root(args.input) if args.input else ROOT
        entries = list_tree(root, excluded_dirs=excluded_dirs)
        if not entries:
            print("NO_ENTRIES")
            log_usage("ls", None, 0, 0)
            return
        joined = "\n".join(entries)
        if not args.task:
            print(joined)
            log_usage("ls", None, len(joined), len(joined))
            return
        model = resolve_model(args.backend, host, args.model)
        result = call_llm_chunked(args.backend, host, model, args.task, joined, args.max_words)
        print(result)
        log_usage("ls", args.backend, len(joined), len(result))
        return

    if args.find:
        root = confine_to_root(args.input) if args.input else ROOT
        matches = find_search(root, args.find, args.ignore_case, excluded_dirs=excluded_dirs)
        if not matches:
            print("NO_MATCHES")
            log_usage("find", None, 0, 0)
            return
        joined = "\n".join(matches)
        if not args.task:
            print(joined)
            log_usage("find", None, len(joined), len(joined))
            return
        model = resolve_model(args.backend, host, args.model)
        result = call_llm_chunked(args.backend, host, model, args.task, joined, args.max_words)
        print(result)
        log_usage("find", args.backend, len(joined), len(result))
        return

    if args.diff:
        diff_path = confine_to_root(args.input) if args.input else None
        diff = get_git_diff(ROOT, diff_path)
        if not diff.strip():
            print("NO_CHANGES")
            log_usage("diff", None, 0, 0)
            return
        if not args.task:
            print(diff, end="" if diff.endswith("\n") else "\n")
            log_usage("diff", None, len(diff), len(diff))
            return
        model = resolve_model(args.backend, host, args.model)
        result = call_llm_chunked(args.backend, host, model, args.task, diff, args.max_words)
        print(result)
        log_usage("diff", args.backend, len(diff), len(result))
        return

    if args.count:
        root = confine_to_root(args.input) if args.input else ROOT
        counted = count_lines(root, excluded_dirs=excluded_dirs)
        if counted is None:
            print("NO_ENTRIES")
            log_usage("count", None, 0, 0)
            return
        if not args.task:
            print(counted)
            log_usage("count", None, len(counted), len(counted))
            return
        model = resolve_model(args.backend, host, args.model)
        result = call_llm_chunked(args.backend, host, model, args.task, counted, args.max_words)
        print(result)
        log_usage("count", args.backend, len(counted), len(result))
        return

    if args.run:
        root = confine_to_root(args.input) if args.input else ROOT
        output = run_package_command(root, args.run)
        if not output.strip():
            print("NO_OUTPUT")
            log_usage("run", None, 0, 0)
            return
        if not args.task:
            print(output, end="" if output.endswith("\n") else "\n")
            log_usage("run", None, len(output), len(output))
            return
        model = resolve_model(args.backend, host, args.model)
        result = call_llm_chunked(args.backend, host, model, args.task, output, args.max_words)
        print(result)
        log_usage("run", args.backend, len(output), len(result))
        return

    if args.grep:
        root = confine_to_root(args.input) if args.input else ROOT
        stats = {}
        matches = grep_search(root, args.grep, args.ignore_case, stats=stats, excluded_dirs=excluded_dirs)
        chars_scanned = stats.get("chars_scanned", 0)
        if not matches:
            print("NO_MATCHES")
            log_usage("grep", None, chars_scanned, 0)
            return
        joined = "\n".join(matches)
        if not args.task:
            print(joined)
            log_usage("grep", None, chars_scanned, len(joined))
            return
        model = resolve_model(args.backend, host, args.model)
        result = call_llm_chunked(args.backend, host, model, args.task, joined, args.max_words)
        print(result)
        log_usage("grep", args.backend, chars_scanned, len(result))
        return

    if not args.task:
        ap.error("--task is required unless --grep/--ls/--find is used")
    model = resolve_model(args.backend, host, args.model)
    content = read_input(args.input)
    result = call_llm_chunked(args.backend, host, model, args.task, content, args.max_words)
    print(result)
    log_usage("task", args.backend, len(content), len(result))


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(0)
