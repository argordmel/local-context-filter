#!/usr/bin/env python3
"""Filter/summarize raw content via a local LLM, keeping only what's relevant to a task.

Usage:
  python3 filter.py --task "TASK" --input FILE_OR_DIR [--backend ollama|lmstudio|openai] [--host URL] [--model TAG] [--max-words N]
  cat file | python3 filter.py --task "TASK"
  python3 filter.py --grep PATTERN [--input DIR] [--task "TASK"]
  python3 filter.py --diff [--input PATH] [--task "TASK"]
  python3 filter.py --log [--limit N] [--input PATH] [--task "TASK"]
  python3 filter.py --ls [--input DIR] [--task "TASK"]
  python3 filter.py --find PATTERN [--input DIR] [--task "TASK"]
  python3 filter.py --run "npm install" [--input DIR] [--task "TASK"]
  python3 filter.py --count [--input FILE_OR_DIR] [--task "TASK"]
  python3 filter.py --image PATH [--format json|md] [--task "TASK"]
  python3 filter.py --report

--input may be a file or a directory (searched recursively, subdirs included).
A relative --input is confined to the current working directory: parent
traversal (../) past it is rejected. An absolute or ~-expanded --input is
also allowed, but only inside your home directory — it becomes its own
trust boundary for that command, so you can point at another project
without cd'ing into it first.

--grep does a real recursive regex search (like `grep -rn`) under --input
(default: cwd; can also be a single file) and prints exact "path:line:
content" matches — no LLM involved, cheapest and most precise option for
exact-pattern search. Add --task on top of --grep to additionally have the
local model narrow a large match list down to what's relevant to that task.

--diff runs `git diff HEAD` (staged + unstaged) at cwd, or scoped to
--input (a single file or directory) if given, and prints it as-is — no
LLM involved. Add --task to have the local model filter it down to what's
relevant to that task instead.

--log runs `git log` (most recent --limit commits, default 50) at cwd, or
scoped to --input (a single file or directory) if given, and prints it
as-is — no LLM involved. Add --task to have the local model summarize it
down to what's relevant to that task instead.

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
output down to what's relevant (e.g. actual errors). --diff, --log, --grep,
--ls, --find, --run, and --count are all mutually exclusive with each other.

--count counts lines per file (like `wc -l`) under --input (default: cwd),
skipping the same excluded dirs as --grep/--ls/--find — no LLM, no file
content in the result, just "N path" per file plus a "N TOTAL" line. Add
--task to have the local model narrow a large listing down to what's
relevant.

--semantic "QUERY" searches by meaning instead of exact match, across the
whole project (not just --input, though --input scopes which results are
shown). Requires --embed-model (no default — pick a dedicated embedding
model, e.g. nomic-embed-text on ollama). Builds/updates a local index at
.claude/semantic-index/index.json (mtime+size cache, only changed files
are re-embedded), then embeds the query and returns the top --top-k (default
10) chunks by cosine similarity as "path:start-end (score): snippet" lines.
No Claude tokens spent on the search itself, only local embedding calls.
Add --task to have the local model further filter the results.

--image PATH extracts metadata (format, dimensions, EXIF, GPS) from an
image via Pillow — no LLM involved. Add --task to also send the image to
a vision-capable model on the selected backend (e.g. ollama's llava/
qwen2.5vl) and fold its answer in as a "description" field. --format
picks json (default) or md for the printed output.

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
import base64
import fnmatch
import json
import math
import os
import re
import shlex
import subprocess
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone

ROOT = os.path.realpath(os.getcwd())
HOME = os.path.realpath(os.path.expanduser("~"))
CONFINE_ROOT = ROOT  # cwd by default; main() may widen this to an absolute/~ --input under HOME
EXCLUDED_DIRS = {".git", "node_modules", "dist", "build", ".venv", "__pycache__", ".next", "coverage"}
MAX_GREP_MATCHES = 500
ALLOWED_RUN_BINS = {"npm", "npx", "pnpm", "yarn"}
RUN_TIMEOUT = 600
RUN_MAX_OUTPUT_CHARS = 24000  # same budget as MAX_CONTENT_CHARS below; caps a noisy install's raw output
USAGE_LOG_TRIM_TRIGGER = 6000  # rotate once the log exceeds this many lines...
USAGE_LOG_KEEP_LINES = 5000    # ...keeping only the most recent this many
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
    "If nothing is relevant, output exactly: NO_RELEVANT_CONTENT — nothing else, no Confidence line. "
    "Otherwise, end your output with a line 'Confidence: high|medium|low — <one-line reason>'. "
    "Only claim high if RAW_CONTENT gave direct, complete evidence for TASK — not an inference or a guess. "
    "Use medium if you found partial or indirect evidence, and low if you're mostly guessing or RAW_CONTENT "
    "barely touches TASK. Do not default to high; most real answers from a fragment of a codebase are medium at best."
)


def list_models(backend, host):
    """Return the model names/ids currently available on the backend, as a
    list preserving the order the server reported them in (for lmstudio/
    openai this is also load order — their /v1/models only lists loaded
    models, first-loaded first).
    """
    url = f"{host}/api/tags" if backend == "ollama" else f"{host}/v1/models"
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            data = json.loads(resp.read())
    except (urllib.error.URLError, ValueError):
        if backend == "ollama":
            start_hint = "`ollama serve`"
        elif backend == "lmstudio":
            start_hint = "LM Studio's local server (Developer tab > Start Server)"
        else:
            start_hint = "the server"
        sys.exit(f"error: {backend} not reachable at {host}. Start it with {start_hint} (or check --host is a valid URL).")
    if backend == "ollama":
        return [name for m in data.get("models", []) if (name := m.get("name"))]
    return [id_ for m in data.get("data", []) if (id_ := m.get("id"))]


VISION_MODEL_HINTS = ("llava", "vision", "vl", "bakllava", "moondream", "minicpm-v", "pixtral")


def is_vision_model(name):
    """Heuristic: does this model name look vision-capable, by substring
    match against VISION_MODEL_HINTS (e.g. "llava", "qwen2.5vl", "llama3.2-
    vision", "moondream")? Name-based only — there's no reliable way to ask
    ollama/lmstudio "can this model see images" up front, so this can both
    miss an unusually-named vision model and (rarely) false-positive on an
    unrelated name containing "vl". Good enough to auto-route --image
    without requiring --model every time; pass --model explicitly to
    override when it guesses wrong.
    """
    lname = name.lower()
    return any(hint in lname for hint in VISION_MODEL_HINTS)


def running_ollama_models(host):
    """Return the names of every model currently loaded in Ollama's memory
    (via /api/ps), in the order the server reported them, or [] if none is
    loaded or the check fails for any reason.

    Never raises — this is a best-effort convenience, not a requirement.
    """
    try:
        with urllib.request.urlopen(f"{host}/api/ps", timeout=3) as resp:
            data = json.loads(resp.read())
    except (urllib.error.URLError, ValueError):
        return []
    return [name for m in data.get("models", []) if (name := m.get("name"))]


def running_lmstudio_models(host):
    """Return the ids of every model currently loaded in LM Studio (via its
    own /api/v0/models, which exposes load state), or [] if none is loaded
    or the check fails for any reason.

    /v1/models lists every downloaded model regardless of load state, so it
    can't answer this. Never raises — this is a best-effort convenience,
    not a requirement.
    """
    try:
        with urllib.request.urlopen(f"{host}/api/v0/models", timeout=3) as resp:
            data = json.loads(resp.read())
    except (urllib.error.URLError, ValueError):
        return []
    return [id_ for m in data.get("data", []) if m.get("state") == "loaded" and (id_ := m.get("id"))]


def resolve_model(backend, host, model, require_vision=False):
    """Validate an explicit --model, or auto-pick one when none was given.

    require_vision=True (used by --image --task) restricts auto-pick to
    models whose name looks vision-capable (see is_vision_model) — this is
    how --image finds the right model automatically when both a text model
    (e.g. qwen2.5:7b) and a vision model (e.g. llava) are loaded/pulled at
    once, without needing --model on every call. An explicit --model is
    never filtered this way — it's just validated for existence, with a
    stderr warning if its name doesn't look vision-capable, in case the
    backend rejects the image outright.

    Auto-pick order for ollama: the running vision/non-vision model (via
    /api/ps — matches whatever's already loaded, no extra load time,
    filtered to vision-hinted names first if require_vision) > (non-vision
    only) the hardcoded default if pulled > alphabetically first pulled
    model in the (possibly vision-filtered) pool.

    Auto-pick for lmstudio: the currently loaded model (via LM Studio's own
    /api/v0/models, which exposes load state — plain /v1/models lists every
    downloaded model, loaded or not, so it can't tell us this), filtered to
    vision-hinted names first if require_vision, > first entry in the pool
    as a last resort.

    Auto-pick for openai (generic OpenAI-compatible servers): first entry
    in the (possibly vision-filtered) /v1/models order — there's no
    standard way to ask a generic server which model is "loaded".

    Exits with a clear error if require_vision and no vision-hinted model
    is available at all.
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
        if require_vision and not is_vision_model(model):
            print(f"warning: '{model}' doesn't look like a vision model by name; the backend may reject the image", file=sys.stderr)
        return model

    if require_vision:
        pool = [n for n in names if is_vision_model(n)]
        if not pool:
            sys.exit(
                f"error: no vision-capable model found on {backend} at {host} "
                f"(looked for names containing: {', '.join(VISION_MODEL_HINTS)}). "
                f"Pull/load one (e.g. `ollama pull llava` or `qwen2.5vl`) or pass --model explicitly."
            )
    else:
        # Exclude vision-hinted models from plain (non-image) auto-pick too,
        # so a vision model left loaded from a prior --image call doesn't
        # get reused for a text/code task just because it's "running" —
        # fall back to the unfiltered list only if every available model
        # happens to look vision-hinted (nothing else to pick from).
        non_vision = [n for n in names if not is_vision_model(n)]
        pool = non_vision if non_vision else names

    if backend == "ollama":
        running = [r for r in running_ollama_models(host) if r in pool]
        if running:
            return running[0]
        if not require_vision and DEFAULT_MODELS["ollama"] in pool:
            return DEFAULT_MODELS["ollama"]
        if pool:
            return sorted(pool)[0]
        sys.exit(f"error: no models available on {backend} at {host}.")
    if backend == "lmstudio":
        running = [r for r in running_lmstudio_models(host) if r in pool]
        if running:
            return running[0]
    if pool:
        return pool[0]
    sys.exit(f"error: no models available on {backend} at {host}.")


def confine_to_root(path):
    """Resolve path and reject anything outside CONFINE_ROOT.

    CONFINE_ROOT is the current working directory (ROOT) by default.
    main() widens it to an absolute or ~-expanded --input path instead,
    but only when that path resolves inside HOME (see set_confine_root) —
    so a relative path stays confined to cwd exactly as before, while an
    absolute/~ path under your home directory becomes its own trust
    boundary for the rest of that command (subdirectories are fine;
    parent traversal past CONFINE_ROOT, or symlinks pointing outside it,
    is rejected either way).
    """
    real = os.path.realpath(os.path.expanduser(path))
    if real != CONFINE_ROOT and not real.startswith(CONFINE_ROOT + os.sep):
        sys.exit(f"error: '{path}' resolves outside the allowed root ({CONFINE_ROOT}); refusing")
    return real


def set_confine_root(input_arg):
    """Widen the module-level CONFINE_ROOT to input_arg if it's an absolute
    or ~-expanded path — but only when it resolves inside HOME. A relative
    --input leaves CONFINE_ROOT at its default (ROOT, the cwd), unchanged.

    Exits with a clear error for an absolute/~ path outside HOME, before
    any file is touched, rather than letting it fail deeper in a walk.
    """
    global CONFINE_ROOT
    if not input_arg:
        return
    expanded = os.path.expanduser(input_arg)
    if not os.path.isabs(expanded):
        return
    real = os.path.realpath(expanded)
    if real != HOME and not real.startswith(HOME + os.sep):
        sys.exit(f"error: absolute/~ --input must be inside your home directory ({HOME}); got '{input_arg}'")
    CONFINE_ROOT = real


def require_existing_input(input_arg, root):
    """Exit with a clear error if --input was given but doesn't exist.

    Without this, --grep/--find/--count on a nonexistent path silently walk
    an empty tree and print the same NO_MATCHES/NO_ENTRIES sentinel as a
    real empty result — indistinguishable from "nothing matched" for the
    caller. --diff is exempt: a deleted file is a legitimate diff target.
    """
    if input_arg and not os.path.exists(root):
        sys.exit(f"error: '{input_arg}' does not exist")


def _is_symlink_escaping_root(path):
    """True if path is a symlink whose target resolves outside CONFINE_ROOT
    — used to skip symlinks found *inside* an already-confined tree during
    a recursive walk (confine_to_root only checks the top-level --input
    path itself, not every entry os.walk turns up underneath it).
    """
    if not os.path.islink(path):
        return False
    real = os.path.realpath(path)
    return real != CONFINE_ROOT and not real.startswith(CONFINE_ROOT + os.sep)


def read_input(path):
    if path:
        real = confine_to_root(path)
        if os.path.isdir(real):
            return read_directory(real)
        if not os.path.exists(real):
            sys.exit(f"error: '{path}' does not exist")
        try:
            with open(real, "r", encoding="utf-8") as f:
                return f.read()
        except UnicodeDecodeError:
            sys.exit(f"error: '{path}' is not a readable text file")
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
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return set()
    exclude = data.get("exclude", [])
    if not isinstance(exclude, list):
        return set()
    return {str(x) for x in exclude}


def _iter_scan_targets(root, excluded):
    """Yield (full_path, display_path) pairs to scan: just root itself
    (basename as display_path) if root is a single file, otherwise every
    file found recursively under root (skipping excluded dirs), display_path
    relative to root.
    """
    if os.path.isfile(root):
        yield root, os.path.basename(root)
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in excluded and not _is_symlink_escaping_root(os.path.join(dirpath, d))]
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            if _is_symlink_escaping_root(full):
                continue
            yield full, os.path.relpath(full, root)


def grep_search(root, pattern, ignore_case=False, stats=None, excluded_dirs=None):
    """Grep for `pattern` (regex) in root — a single file, or recursively
    under root if it's a directory, skipping excluded_dirs (default:
    EXCLUDED_DIRS) and binary files.

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
    for full, rel in _iter_scan_targets(root, excluded):
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
        print(f"warning: hit {MAX_GREP_MATCHES}-match cap, results truncated; narrow the pattern or --input path", file=sys.stderr)
    if stats is not None:
        stats["chars_scanned"] = chars_scanned
    return matches


def find_search(root, pattern, ignore_case=False, excluded_dirs=None):
    """Find files/dirs whose basename matches a glob `pattern` (like `find -iname`),
    skipping excluded_dirs (default: EXCLUDED_DIRS). Directories are suffixed
    with '/'. No LLM, no file contents read — only names are compared.

    If root is a single file, just tests that file's own basename against
    the pattern instead of walking (os.walk yields nothing for a file path).
    """
    excluded = EXCLUDED_DIRS if excluded_dirs is None else excluded_dirs
    check_pattern = pattern.lower() if ignore_case else pattern
    if os.path.isfile(root):
        name = os.path.basename(root)
        check_name = name.lower() if ignore_case else name
        return [name] if fnmatch.fnmatch(check_name, check_pattern) else []
    matches = []
    truncated = False
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames if d not in excluded and not _is_symlink_escaping_root(os.path.join(dirpath, d))
        )
        filenames = [f for f in filenames if not _is_symlink_escaping_root(os.path.join(dirpath, f))]
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
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except (OSError, UnicodeDecodeError):
        return "NO_USAGE_DATA"
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

    by_day = {}
    for e in entries:
        ts = e.get("ts") or ""
        try:
            local_dt = datetime.fromisoformat(ts).astimezone()
        except ValueError:
            continue
        day = local_dt.strftime("%Y-%m-%d")
        agg = by_day.setdefault(day, {"runs": 0, "tokens_saved_est": 0})
        agg["runs"] += 1
        agg["tokens_saved_est"] += e.get("tokens_saved_est") or 0

    last_7_days = sorted(by_day)[-7:]
    if last_7_days:
        lines.append("")
        lines.append("Last 7 days:")
        for day in last_7_days:
            agg = by_day[day]
            weekday = datetime.strptime(day, "%Y-%m-%d").strftime("%A").lower()
            lines.append(f"{weekday} | {agg['runs']} runs | {format_tokens_short(agg['tokens_saved_est'])} tokens")

    return "\n".join(lines)


def format_tokens_short(n):
    """Abbreviate a token count as e.g. 100K, 1M, 950 for compact table display."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}".rstrip("0").rstrip(".") + "M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)


def rotate_usage_log_if_needed(path):
    """Trim LOG_PATH to the most recent USAGE_LOG_KEEP_LINES lines once it
    exceeds USAGE_LOG_TRIM_TRIGGER lines, so it never grows unbounded.

    Best-effort, like log_usage: any failure is silently ignored.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > USAGE_LOG_TRIM_TRIGGER:
            with open(path, "w", encoding="utf-8") as f:
                f.writelines(lines[-USAGE_LOG_KEEP_LINES:])
    except (OSError, UnicodeDecodeError):
        pass


def log_usage(mode, backend, chars_in, chars_out):
    """Append one JSON line to LOG_PATH with local-only usage metadata.

    No file contents, paths, or task text are recorded — only counts. Never
    raises: a logging failure (e.g. read-only filesystem) must not break the
    actual command. Rotates the log (see rotate_usage_log_if_needed) once it
    grows past USAGE_LOG_TRIM_TRIGGER lines — old entries are dropped, but
    --report's totals only ever reflect what's currently in the file anyway,
    so this just bounds disk use without changing what --report can show.
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
    rotate_usage_log_if_needed(LOG_PATH)


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
            with open(path, "rb") as f:
                content = f.read()
            content.decode("utf-8")
        except (UnicodeDecodeError, OSError):
            return None
        return content.count(b"\n")

    if os.path.isfile(root):
        n = line_count(root)
        if n is None:
            sys.exit(f"error: '{root}' is not a readable text file")
        return f"{n} {os.path.basename(root)}"

    lines = []
    total = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames if d not in excluded and not _is_symlink_escaping_root(os.path.join(dirpath, d))
        )
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            if _is_symlink_escaping_root(full):
                continue
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
        dirnames[:] = sorted(
            d for d in dirnames if d not in excluded and not _is_symlink_escaping_root(os.path.join(dirpath, d))
        )
        rel_dir = os.path.relpath(dirpath, root)
        if rel_dir != ".":
            entries.append(rel_dir + "/")
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            if _is_symlink_escaping_root(full):
                continue
            entries.append(os.path.relpath(full, root))
    return sorted(entries)


def read_directory(root):
    """Recursively read all files under root (subdirs included), each tagged by relative path."""
    chunks = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not _is_symlink_escaping_root(os.path.join(dirpath, d))]
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            if _is_symlink_escaping_root(full):
                continue
            rel = os.path.relpath(full, root)
            try:
                with open(full, "r", encoding="utf-8") as f:
                    chunks.append(f"--- {rel} ---\n{f.read()}")
            except (UnicodeDecodeError, OSError):
                continue
    if not chunks:
        sys.exit(f"error: no readable text files under '{root}'")
    return "\n\n".join(chunks)


def require_git_repo(git_cwd):
    """Exit with an error unless git_cwd is inside a git work tree."""
    try:
        check = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"], cwd=git_cwd, capture_output=True, text=True, timeout=10,
        )
    except FileNotFoundError:
        sys.exit("error: git not found on PATH")
    except PermissionError:
        sys.exit("error: git found on PATH but is not executable")
    if check.returncode != 0:
        sys.exit(f"error: '{git_cwd}' is not inside a git repository")


def get_git_diff(root, path=None):
    """Return `git diff HEAD` (staged + unstaged) at root, as text.

    If path is given, scopes the diff to that file/directory (must already
    be confined to root by the caller).

    root itself may be a single file (an absolute/~ --input can widen
    CONFINE_ROOT straight to a file) — git needs a directory as cwd, so
    that case runs from root's parent directory instead; path's pathspec
    is still computed relative to that same parent, so scoping is unaffected.
    """
    git_cwd = root if os.path.isdir(root) else os.path.dirname(root)
    require_git_repo(git_cwd)
    cmd = ["git", "diff", "HEAD"]
    if path:
        cmd += ["--", os.path.relpath(path, git_cwd)]
    result = subprocess.run(cmd, cwd=git_cwd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        sys.exit(f"error: git diff failed: {result.stderr.strip()}")
    return result.stdout


def get_git_log(root, path=None, limit=50):
    """Return the most recent `limit` `git log` commits at root, as text.

    If path is given, scopes the log to that file/directory's history (must
    already be confined to root by the caller). Mirrors get_git_diff's
    single-file root handling.
    """
    git_cwd = root if os.path.isdir(root) else os.path.dirname(root)
    require_git_repo(git_cwd)
    cmd = ["git", "log", f"-{limit}"]
    if path:
        cmd += ["--", os.path.relpath(path, git_cwd)]
    result = subprocess.run(cmd, cwd=git_cwd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        if "does not have any commits yet" in result.stderr:
            return ""
        sys.exit(f"error: git log failed: {result.stderr.strip()}")
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
    if not os.path.isdir(root):
        sys.exit(f"error: '{root}' is not a directory")
    try:
        result = subprocess.run(
            argv, cwd=root, capture_output=True, text=True, timeout=RUN_TIMEOUT,
        )
    except FileNotFoundError:
        sys.exit(f"error: '{bin_name}' not found on PATH")
    except PermissionError:
        sys.exit(f"error: '{bin_name}' found on PATH but is not executable")
    except subprocess.TimeoutExpired:
        sys.exit(f"error: --run command timed out after {RUN_TIMEOUT}s")
    output = result.stdout + result.stderr
    if result.returncode != 0:
        output += f"\n[exit code {result.returncode}]"
    return output


MAX_CONTENT_CHARS = 24000  # ~6k tokens; keeps prompt+content under num_ctx below with room for output
NUM_CTX = 8192


def _post_json(backend, url, payload, timeout=120):
    """POST payload as JSON to url and return the parsed response.

    Shared by call_llm and call_embed so both get identical error handling
    (unreachable backend, HTTP error, malformed JSON) and error wording.
    """
    try:
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"}
        )
    except ValueError as e:
        sys.exit(f"error: invalid --host URL '{url}': {e}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(body).get("error", {}).get("message", body)
        except (json.JSONDecodeError, AttributeError):
            pass
        sys.exit(f"error: {backend} at {url} returned {e.code}: {body}")
    except urllib.error.URLError as e:
        sys.exit(f"error: cannot reach {backend} at {url} ({e}).")
    except json.JSONDecodeError:
        sys.exit(f"error: {backend} at {url} returned a response that isn't valid JSON")


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

    data = _post_json(backend, url, payload)

    if backend == "ollama":
        return data.get("response", "").strip()
    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError):
        sys.exit(f"error: {backend} at {url} returned an unexpected response shape: {json.dumps(data)[:300]}")


CHUNK_LINES = 50
CHUNK_OVERLAP = 10
SEMANTIC_INDEX_RELPATH = os.path.join(".claude", "semantic-index", "index.json")


def call_embed(backend, host, model, text):
    """Embed text via the backend's embedding endpoint, returning a vector
    (list of floats). Ollama: POST /api/embeddings. lmstudio/openai
    (OpenAI-compatible): POST /v1/embeddings.
    """
    if backend == "ollama":
        url = f"{host}/api/embeddings"
        payload = {"model": model, "prompt": text}
    else:
        url = f"{host}/v1/embeddings"
        payload = {"model": model, "input": text}
    data = _post_json(backend, url, payload)
    if backend == "ollama":
        vector = data.get("embedding")
    else:
        try:
            vector = data["data"][0]["embedding"]
        except (KeyError, IndexError, TypeError):
            vector = None
    if not vector:
        sys.exit(f"error: {backend} at {url} returned no embedding vector for model '{model}'")
    return vector


def require_model_available(backend, host, model):
    """Exit with a clear error unless `model` is available on backend.

    Unlike resolve_model, never auto-picks a fallback — an embedding model
    is a deliberate choice (--embed-model has no default), so an invalid
    or missing one is always an error, not a silent substitution.
    """
    names = list_models(backend, host)
    if model not in names:
        if backend == "ollama":
            fix = f"`ollama pull {model}`"
        elif backend == "lmstudio":
            fix = "load it in LM Studio first"
        else:
            fix = "load it on the server first"
        sys.exit(f"error: model '{model}' not available on {backend}. {fix} (available: {', '.join(sorted(names)) or 'none'}).")


def cosine_sim(a, b):
    """Cosine similarity between two equal-length numeric vectors, pure Python
    (no numpy dependency). Returns 0.0 if either vector is all-zero.
    """
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def chunk_lines_with_overlap(lines, chunk_lines=CHUNK_LINES, overlap=CHUNK_OVERLAP):
    """Split a list of lines into overlapping (start, end, text) chunks,
    1-indexed inclusive line numbers. Empty input yields no chunks.
    """
    step = chunk_lines - overlap
    n = len(lines)
    chunks = []
    i = 0
    while i < n:
        end = min(i + chunk_lines, n)
        chunks.append((i + 1, end, "".join(lines[i:end])))
        if end == n:
            break
        i += step
    return chunks


def make_snippet(text, max_chars=200):
    """First up to 3 non-blank lines of a chunk, joined and truncated —
    just enough to eyeball relevance without re-reading the file.
    """
    snippet_lines = [ln.strip() for ln in text.splitlines() if ln.strip()][:3]
    return " / ".join(snippet_lines)[:max_chars]


def semantic_index_path(root):
    return os.path.join(root, SEMANTIC_INDEX_RELPATH)


def load_semantic_index(path, backend, model):
    """Load the semantic index at path, or a fresh empty one if missing,
    corrupt, or built for a different backend/model/chunking config —
    vectors from a different embedding model aren't comparable, so a
    mismatch means starting over rather than mixing incompatible vectors.
    """
    empty = {
        "backend": backend, "model": model,
        "chunk_lines": CHUNK_LINES, "chunk_overlap": CHUNK_OVERLAP,
        "files": {},
    }
    if not os.path.isfile(path):
        return empty
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return empty
    if not isinstance(data, dict) or not isinstance(data.get("files"), dict):
        return empty
    if (data.get("backend") != backend or data.get("model") != model
            or data.get("chunk_lines") != CHUNK_LINES or data.get("chunk_overlap") != CHUNK_OVERLAP):
        return empty
    return data


def save_semantic_index(path, index):
    """Best-effort save — a write failure (e.g. read-only disk) doesn't
    break the search itself, just means the next run rebuilds from scratch.
    """
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(index, f)
    except OSError:
        pass


def update_semantic_index(root, excluded_dirs, backend, host, embed_model, index):
    """Refresh index in place against the files currently on disk under root:
    unchanged files (same mtime+size) are skipped, new/modified files are
    re-chunked and re-embedded, and entries for deleted files are dropped.
    """
    seen = set()
    for full, rel in _iter_scan_targets(root, excluded_dirs):
        try:
            with open(full, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except (UnicodeDecodeError, OSError):
            continue
        try:
            stat = os.stat(full)
        except OSError:
            continue
        seen.add(rel)
        existing = index["files"].get(rel)
        if existing and existing.get("mtime") == stat.st_mtime and existing.get("size") == stat.st_size:
            continue
        new_chunks = []
        for start, end, text in chunk_lines_with_overlap(lines):
            vector = call_embed(backend, host, embed_model, text)
            new_chunks.append({"start": start, "end": end, "vector": vector, "snippet": make_snippet(text)})
        index["files"][rel] = {"mtime": stat.st_mtime, "size": stat.st_size, "chunks": new_chunks}
    for rel in list(index["files"]):
        if rel not in seen:
            del index["files"][rel]
    return index


def semantic_search(index, query_vector, top_k, allowed_prefix=None):
    """Rank every chunk in index by cosine similarity to query_vector,
    return the top_k as (score, rel_path, start_line, end_line, snippet).

    allowed_prefix, if given, restricts results to rel paths equal to it
    or nested under it (a relative --input scoping which results show,
    without narrowing what the index itself covers).
    """
    results = []
    for rel, entry in index.get("files", {}).items():
        if allowed_prefix is not None and rel != allowed_prefix and not rel.startswith(allowed_prefix + os.sep):
            continue
        for chunk in entry.get("chunks", []):
            vector = chunk.get("vector")
            if not vector:
                continue
            score = cosine_sim(query_vector, vector)
            results.append((score, rel, chunk["start"], chunk["end"], chunk.get("snippet", "")))
    results.sort(key=lambda r: -r[0])
    return results[:top_k]


def compute_file_coverage(matches, output):
    """Given --grep matches ("path:line: content" strings) and the local
    model's filtered output, return (covered, total, missing) — how many of
    the distinct source files from matches are actually mentioned in output.

    This is a mechanical, independent check: the model's own self-reported
    Confidence line can be (and in testing has been) overconfident, so
    Claude gets an objective signal of possible dropped evidence alongside
    it, not just the model's word for it.
    """
    files = []
    seen = set()
    for m in matches:
        path = m.split(":", 1)[0]
        if path not in seen:
            seen.add(path)
            files.append(path)
    missing = [f for f in files if f not in output]
    covered = len(files) - len(missing)
    return covered, len(files), missing


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


IMAGE_MIME_MAP = {
    "JPEG": "image/jpeg", "PNG": "image/png", "GIF": "image/gif",
    "WEBP": "image/webp", "BMP": "image/bmp", "TIFF": "image/tiff",
}


def _jsonable(value):
    """Coerce a Pillow/EXIF value into something json.dumps can handle
    (bytes -> str, IFDRational/etc -> float/str), recursing into
    dicts/lists/tuples. Falls back to str() for anything else exotic.
    """
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    try:
        json.dumps(value)
        return value
    except TypeError:
        try:
            return float(value)
        except (TypeError, ValueError):
            return str(value)


def extract_image_metadata(path):
    """Return a dict of no-LLM image metadata: format, mode, dimensions,
    file size, and decoded EXIF/GPS tags (if present). Exits with a clear
    error if Pillow isn't installed or the file isn't a readable image.
    """
    try:
        from PIL import Image, ExifTags
    except ImportError:
        sys.exit("error: Pillow not installed. Run: pip3 install Pillow")
    try:
        img = Image.open(path)
        img.load()
    except Exception as e:
        sys.exit(f"error: '{path}' is not a readable image file ({e})")

    meta = {
        "file": os.path.basename(path),
        "format": img.format,
        "mode": img.mode,
        "width": img.width,
        "height": img.height,
        "size_bytes": os.path.getsize(path),
    }

    exif_data = {}
    gps_data = {}
    try:
        exif = img.getexif()
    except Exception:
        exif = None
    if exif:
        for tag_id, value in exif.items():
            tag = ExifTags.TAGS.get(tag_id, tag_id)
            if tag == "GPSInfo":
                try:
                    gps_ifd = exif.get_ifd(tag_id)
                except Exception:
                    gps_ifd = value if isinstance(value, dict) else {}
                for gps_id, gps_value in gps_ifd.items():
                    gps_tag = ExifTags.GPSTAGS.get(gps_id, gps_id)
                    gps_data[str(gps_tag)] = _jsonable(gps_value)
            else:
                exif_data[str(tag)] = _jsonable(value)
    if exif_data:
        meta["exif"] = exif_data
    if gps_data:
        meta["gps"] = gps_data
    return meta


def image_to_base64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


def call_vision_llm(backend, host, model, task, image_b64, image_format, max_words):
    """Ask a vision-capable model on backend to describe/analyze an image.

    ollama: POST /api/generate with raw base64 in "images" (no data-URI
    prefix). lmstudio/openai: OpenAI-compatible chat completions with an
    image_url content part using a data: URI (mime picked from
    image_format, falling back to image/jpeg for an unrecognized one).
    """
    prompt_text = f"{task}\n\nKeep the output under {max_words} words."
    if backend == "ollama":
        url = f"{host}/api/generate"
        payload = {
            "model": model,
            "prompt": prompt_text,
            "images": [image_b64],
            "stream": False,
            "options": {"temperature": 0.2},
        }
    else:
        mime = IMAGE_MIME_MAP.get((image_format or "").upper(), "image/jpeg")
        url = f"{host}/v1/chat/completions"
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt_text},
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_b64}"}},
                    ],
                }
            ],
            "temperature": 0.2,
            "stream": False,
        }
    data = _post_json(backend, url, payload, timeout=180)
    if backend == "ollama":
        return data.get("response", "").strip()
    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError):
        sys.exit(f"error: {backend} at {url} returned an unexpected response shape: {json.dumps(data)[:300]}")


OCR_TIMEOUT = 60


def run_tesseract(image_path, lang="eng"):
    """Run the tesseract CLI on image_path and return the extracted raw
    text (stdout), or exit with a clear error if tesseract isn't
    installed, the language pack isn't available, or it fails/times out.

    Uses the CLI directly (not pytesseract) so --ocr needs no extra Python
    dependency beyond the `tesseract` binary itself
    (`brew install tesseract`, or `apt install tesseract-ocr`).
    """
    try:
        result = subprocess.run(
            ["tesseract", image_path, "stdout", "-l", lang],
            capture_output=True, text=True, timeout=OCR_TIMEOUT,
        )
    except FileNotFoundError:
        sys.exit("error: 'tesseract' not found on PATH. Install it: `brew install tesseract` (macOS) or `apt install tesseract-ocr` (Linux).")
    except subprocess.TimeoutExpired:
        sys.exit(f"error: tesseract timed out after {OCR_TIMEOUT}s")
    if result.returncode != 0:
        stderr = result.stderr.strip()
        if "Failed loading language" in stderr:
            sys.exit(
                f"error: tesseract language pack '{lang}' not installed. "
                f"Install it (e.g. `brew install tesseract-lang` on macOS for extra languages, "
                f"or download the .traineddata into tesseract's tessdata dir) or pass --lang eng."
            )
        sys.exit(f"error: tesseract failed: {stderr}")
    return result.stdout


def format_metadata_md(meta):
    """Render an --image metadata dict as Markdown."""
    lines = [f"# {meta['file']}", ""]
    lines.append(f"- Format: {meta.get('format')}")
    lines.append(f"- Mode: {meta.get('mode')}")
    lines.append(f"- Dimensions: {meta.get('width')}x{meta.get('height')}")
    lines.append(f"- Size: {meta.get('size_bytes')} bytes")
    if "ocr_text" in meta:
        lines.append("")
        lines.append("## OCR Text")
        lines.append(meta["ocr_text"] if meta["ocr_text"] else "_(no text detected)_")
    if "description" in meta:
        lines.append("")
        lines.append("## Description")
        lines.append(meta["description"])
    if meta.get("exif"):
        lines.append("")
        lines.append("## EXIF")
        for k, v in meta["exif"].items():
            lines.append(f"- {k}: {v}")
    if meta.get("gps"):
        lines.append("")
        lines.append("## GPS")
        for k, v in meta["gps"].items():
            lines.append(f"- {k}: {v}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--task", help="what the filtered content will be used for (required unless --grep is used alone)")
    ap.add_argument("--input", help="file or directory to filter/search (recursive); with --diff, scopes the diff to this path; omit to read stdin (LLM mode) or diff the whole repo (--diff)")
    ap.add_argument("--grep", metavar="PATTERN", help="exact regex search (like grep -rn) under --input, no LLM")
    ap.add_argument("--ignore-case", action="store_true", help="case-insensitive --grep")
    ap.add_argument("--diff", action="store_true", help="filter `git diff HEAD` at cwd (or --input path); no LLM without --task")
    ap.add_argument("--log", action="store_true", help="filter `git log` at cwd (or --input path); no LLM without --task")
    ap.add_argument("--limit", type=int, default=50, metavar="N", help="max commits for --log (default: 50)")
    ap.add_argument("--ls", action="store_true", help="recursively list files/dirs under --input (default: cwd), no LLM without --task")
    ap.add_argument("--find", metavar="PATTERN", help="find files/dirs by basename glob (like `find -iname`) under --input, no LLM")
    ap.add_argument("--run", metavar="COMMAND", help="run an npm/npx/pnpm/yarn command (e.g. 'npm install') at --input (default: cwd) and print its output; no LLM without --task")
    ap.add_argument("--count", action="store_true", help="count lines per file under --input (default: cwd), like `wc -l`; no LLM without --task")
    ap.add_argument("--image", metavar="PATH", help="extract image metadata (EXIF/dimensions) via Pillow, no LLM; add --task to also get a vision-model description")
    ap.add_argument("--format", choices=["json", "md"], default="json", help="output format for --image (default: json)")
    ap.add_argument("--ocr", action="store_true", help="run classic OCR (tesseract) on --image and include the raw text; with --task, extracts from that text via the text model instead of a vision model")
    ap.add_argument("--lang", default="eng", help="tesseract language(s) for --ocr, e.g. 'eng', 'spa', 'spa+eng' (default: eng)")
    ap.add_argument("--semantic", metavar="QUERY", help="search by meaning across the whole project (--input only scopes shown results); requires --embed-model")
    ap.add_argument("--embed-model", help="embedding model tag/id, required with --semantic (no default)")
    ap.add_argument("--top-k", type=int, default=10, metavar="N", help="max results for --semantic (default: 10)")
    ap.add_argument("--report", action="store_true", help="print a summary of usage.json (local totals); cannot combine with anything else")
    ap.add_argument("--clean", action="store_true", help="delete usage.json; cannot combine with anything else")
    ap.add_argument("--backend", choices=["ollama", "lmstudio", "openai"], default="ollama", help="local LLM server (default: ollama)")
    ap.add_argument("--host", help="override backend host (default: ollama=http://localhost:11434, lmstudio=http://localhost:1234; required for --backend openai)")
    ap.add_argument("--model", help="model tag/id; default: qwen2.5:7b on ollama, first available model on lmstudio/openai")
    ap.add_argument("--max-words", type=int, default=300, help="target max words of output (default: 300)")
    args = ap.parse_args()

    other_modes = (
        args.diff or args.log or bool(args.grep) or args.ls or bool(args.find) or bool(args.run)
        or args.count or bool(args.semantic) or bool(args.image) or bool(args.task) or bool(args.input)
        or args.ocr or args.format != "json" or args.lang != "eng"
    )
    if args.report and other_modes:
        ap.error("--report cannot be combined with --diff/--log/--grep/--ls/--find/--run/--count/--semantic/--image/--ocr/--format/--lang/--task/--input")
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

    if sum([args.diff, args.log, bool(args.grep), args.ls, bool(args.find), bool(args.run), args.count, bool(args.semantic), bool(args.image)]) > 1:
        ap.error("--diff, --log, --grep, --ls, --find, --run, --count, --semantic, and --image are mutually exclusive")
    if args.semantic and not args.embed_model:
        ap.error("--embed-model is required with --semantic")
    if args.semantic and args.top_k < 1:
        ap.error("--top-k must be a positive integer")
    if args.format != "json" and not args.image:
        ap.error("--format is only used with --image")
    if args.ocr and not args.image:
        ap.error("--ocr is only used with --image")
    if args.lang != "eng" and not args.ocr:
        ap.error("--lang is only used with --ocr")
    if args.image and args.input:
        ap.error("--input is not used with --image; pass the image path to --image directly")
    if args.backend == "openai" and not args.host:
        ap.error("--host is required for --backend openai (no conventional default port)")
    host = args.host or DEFAULT_HOSTS[args.backend]
    set_confine_root(args.input)
    if args.image:
        set_confine_root(args.image)
    excluded_dirs = EXCLUDED_DIRS | load_project_excludes(CONFINE_ROOT)

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
        require_existing_input(args.input, root)
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
        diff = get_git_diff(CONFINE_ROOT, diff_path)
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

    if args.log:
        if args.limit < 1:
            ap.error("--limit must be a positive integer")
        log_path = confine_to_root(args.input) if args.input else None
        git_log = get_git_log(CONFINE_ROOT, log_path, args.limit)
        if not git_log.strip():
            print("NO_COMMITS")
            log_usage("log", None, 0, 0)
            return
        if not args.task:
            print(git_log, end="" if git_log.endswith("\n") else "\n")
            log_usage("log", None, len(git_log), len(git_log))
            return
        model = resolve_model(args.backend, host, args.model)
        result = call_llm_chunked(args.backend, host, model, args.task, git_log, args.max_words)
        print(result)
        log_usage("log", args.backend, len(git_log), len(result))
        return

    if args.count:
        root = confine_to_root(args.input) if args.input else ROOT
        require_existing_input(args.input, root)
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
            printed = output
            if len(printed) > RUN_MAX_OUTPUT_CHARS:
                printed = printed[:RUN_MAX_OUTPUT_CHARS]
                print(
                    f"warning: --run output is {len(output)} chars, truncated to {RUN_MAX_OUTPUT_CHARS}; "
                    f"add --task to have the local model filter it down instead",
                    file=sys.stderr,
                )
            print(printed, end="" if printed.endswith("\n") else "\n")
            log_usage("run", None, len(output), len(printed))
            return
        model = resolve_model(args.backend, host, args.model)
        result = call_llm_chunked(args.backend, host, model, args.task, output, args.max_words)
        print(result)
        log_usage("run", args.backend, len(output), len(result))
        return

    if args.semantic:
        require_model_available(args.backend, host, args.embed_model)
        root = confine_to_root(args.input) if args.input else ROOT
        require_existing_input(args.input, root)
        index_path = semantic_index_path(CONFINE_ROOT)
        index = load_semantic_index(index_path, args.backend, args.embed_model)
        index = update_semantic_index(CONFINE_ROOT, excluded_dirs, args.backend, host, args.embed_model, index)
        save_semantic_index(index_path, index)
        query_vector = call_embed(args.backend, host, args.embed_model, args.semantic)
        allowed_prefix = None
        if args.input:
            rel_input = os.path.relpath(root, CONFINE_ROOT)
            allowed_prefix = None if rel_input == "." else rel_input
        results = semantic_search(index, query_vector, args.top_k, allowed_prefix)
        if not results:
            print("NO_MATCHES")
            log_usage("semantic", None, 0, 0)
            return
        joined = "\n".join(f"{rel}:{start}-{end} ({score:.2f}): {snippet}" for score, rel, start, end, snippet in results)
        if not args.task:
            print(joined)
            log_usage("semantic", args.backend, 0, len(joined))
            return
        model = resolve_model(args.backend, host, args.model)
        result = call_llm_chunked(args.backend, host, model, args.task, joined, args.max_words)
        print(result)
        log_usage("semantic", args.backend, len(joined), len(result))
        return

    if args.image:
        image_path = confine_to_root(args.image)
        if not os.path.isfile(image_path):
            sys.exit(f"error: '{args.image}' does not exist or is not a file")
        meta = extract_image_metadata(image_path)
        if args.ocr:
            meta["ocr_text"] = run_tesseract(image_path, args.lang).strip()
        if args.task:
            if args.ocr:
                if meta["ocr_text"]:
                    model = resolve_model(args.backend, host, args.model)
                    meta["description"] = call_llm_chunked(args.backend, host, model, args.task, meta["ocr_text"], args.max_words)
                else:
                    print("warning: --ocr found no text, skipping --task extraction (nothing to feed the text model)", file=sys.stderr)
            else:
                model = resolve_model(args.backend, host, args.model, require_vision=True)
                b64 = image_to_base64(image_path)
                meta["description"] = call_vision_llm(
                    args.backend, host, model, args.task, b64, meta.get("format"), args.max_words
                )
        output = json.dumps(meta, indent=2, ensure_ascii=False) if args.format == "json" else format_metadata_md(meta)
        print(output)
        log_usage("image", args.backend if args.task else None, os.path.getsize(image_path), len(output))
        return

    if args.grep:
        root = confine_to_root(args.input) if args.input else ROOT
        require_existing_input(args.input, root)
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
        if result != "NO_RELEVANT_CONTENT":
            covered, total, missing = compute_file_coverage(matches, result)
            coverage_line = f"coverage: {covered}/{total} source files referenced in this summary"
            if missing:
                coverage_line += f" (missing: {', '.join(missing)})"
            print(coverage_line)
        log_usage("grep", args.backend, chars_scanned, len(result))
        return

    if not args.task:
        ap.error("--task is required unless --grep/--ls/--find/--semantic/--image is used")
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
