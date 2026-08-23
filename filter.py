#!/usr/bin/env python3
"""Filter/summarize raw content via a local LLM, keeping only what's relevant to a task.

Usage:
  python3 filter.py --task "TASK" --input FILE_OR_DIR [--backend ollama|lmstudio|openai] [--host URL] [--model TAG] [--max-words N]
  cat file | python3 filter.py --task "TASK"
  python3 filter.py --grep PATTERN [--input DIR] [--task "TASK"]
  python3 filter.py --diff [--task "TASK"]

--input may be a file or a directory (searched recursively, subdirs included).
Paths are confined to the current working directory: parent traversal (../,
paths outside cwd) is rejected.

--grep does a real recursive regex search (like `grep -rn`) under --input
(default: cwd) and prints exact "path:line: content" matches — no LLM
involved, cheapest and most precise option for exact-pattern search. Add
--task on top of --grep to additionally have the local model narrow a large
match list down to what's relevant to that task.

--diff runs `git diff HEAD` (staged + unstaged) at cwd and prints it as-is —
no LLM involved. Add --task to have the local model filter it down to what's
relevant to that task instead. Cannot combine with --grep or --input.

--backend selects the local server: "ollama" (default, http://localhost:11434),
"lmstudio" (OpenAI-compatible, http://localhost:1234), or "openai" (any other
OpenAI-compatible server — llama.cpp server, vLLM, etc.) which requires
--host since there's no conventional default port. Override ollama/lmstudio's
default host with --host too.

Prints the filtered content to stdout. Nothing else goes to stdout.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
import urllib.error

ROOT = os.path.realpath(os.getcwd())
EXCLUDED_DIRS = {".git", "node_modules", "dist", "build", ".venv", "__pycache__", ".next", "coverage"}
MAX_GREP_MATCHES = 500

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


def resolve_model(backend, host, model):
    """Validate an explicit --model, or auto-pick one when none was given."""
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


def grep_search(root, pattern, ignore_case=False):
    """Recursively grep for `pattern` (regex) under root, skipping EXCLUDED_DIRS and binary files."""
    flags = re.IGNORECASE if ignore_case else 0
    rx = re.compile(pattern, flags)
    matches = []
    truncated = False
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS]
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root)
            try:
                with open(full, "r", encoding="utf-8") as f:
                    for lineno, line in enumerate(f, start=1):
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
    return matches


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


def get_git_diff(root):
    """Return `git diff HEAD` (staged + unstaged) at root, as text."""
    try:
        result = subprocess.run(
            ["git", "diff", "HEAD"], cwd=root, capture_output=True, text=True, timeout=30
        )
    except FileNotFoundError:
        sys.exit("error: git not found on PATH")
    if result.returncode != 0:
        sys.exit(f"error: git diff failed: {result.stderr.strip()}")
    return result.stdout


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


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--task", help="what the filtered content will be used for (required unless --grep is used alone)")
    ap.add_argument("--input", help="file or directory to filter/search (recursive); omit to read stdin")
    ap.add_argument("--grep", metavar="PATTERN", help="exact regex search (like grep -rn) under --input, no LLM")
    ap.add_argument("--ignore-case", action="store_true", help="case-insensitive --grep")
    ap.add_argument("--diff", action="store_true", help="filter `git diff HEAD` at cwd; no LLM without --task")
    ap.add_argument("--backend", choices=["ollama", "lmstudio", "openai"], default="ollama", help="local LLM server (default: ollama)")
    ap.add_argument("--host", help="override backend host (default: ollama=http://localhost:11434, lmstudio=http://localhost:1234; required for --backend openai)")
    ap.add_argument("--model", help="model tag/id; default: qwen2.5:7b on ollama, first available model on lmstudio/openai")
    ap.add_argument("--max-words", type=int, default=300, help="target max words of output (default: 300)")
    args = ap.parse_args()
    if args.backend == "openai" and not args.host:
        ap.error("--host is required for --backend openai (no conventional default port)")
    host = args.host or DEFAULT_HOSTS[args.backend]

    if args.diff:
        if args.grep or args.input:
            ap.error("--diff cannot be combined with --grep or --input")
        diff = get_git_diff(ROOT)
        if not diff.strip():
            print("NO_CHANGES")
            return
        if not args.task:
            print(diff, end="" if diff.endswith("\n") else "\n")
            return
        model = resolve_model(args.backend, host, args.model)
        result = call_llm(args.backend, host, model, args.task, diff, args.max_words)
        print(result)
        return

    if args.grep:
        root = confine_to_root(args.input) if args.input else ROOT
        matches = grep_search(root, args.grep, args.ignore_case)
        if not matches:
            print("NO_MATCHES")
            return
        if not args.task:
            print("\n".join(matches))
            return
        model = resolve_model(args.backend, host, args.model)
        result = call_llm(args.backend, host, model, args.task, "\n".join(matches), args.max_words)
        print(result)
        return

    if not args.task:
        ap.error("--task is required unless --grep is used")
    model = resolve_model(args.backend, host, args.model)
    content = read_input(args.input)
    result = call_llm(args.backend, host, model, args.task, content, args.max_words)
    print(result)


if __name__ == "__main__":
    main()
