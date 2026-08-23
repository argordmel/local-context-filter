#!/usr/bin/env python3
"""Filter/summarize raw content via local Ollama model, keeping only what's relevant to a task.

Usage:
  python3 filter.py --task "TASK" --input FILE_OR_DIR [--model qwen2.5:7b] [--max-words N]
  cat file | python3 filter.py --task "TASK"
  python3 filter.py --grep PATTERN [--input DIR] [--task "TASK"]

--input may be a file or a directory (searched recursively, subdirs included).
Paths are confined to the current working directory: parent traversal (../,
paths outside cwd) is rejected.

--grep does a real recursive regex search (like `grep -rn`) under --input
(default: cwd) and prints exact "path:line: content" matches — no LLM
involved, cheapest and most precise option for exact-pattern search. Add
--task on top of --grep to additionally have the local model narrow a large
match list down to what's relevant to that task.

Prints the filtered content to stdout. Nothing else goes to stdout.
"""
import argparse
import json
import os
import re
import sys
import urllib.request
import urllib.error

OLLAMA_HOST = "http://localhost:11434"
OLLAMA_URL = f"{OLLAMA_HOST}/api/generate"
ROOT = os.path.realpath(os.getcwd())
EXCLUDED_DIRS = {".git", "node_modules", "dist", "build", ".venv", "__pycache__", ".next", "coverage"}
MAX_GREP_MATCHES = 500

SYSTEM_PROMPT = (
    "You are a context filter. You receive a TASK and RAW_CONTENT. "
    "Output ONLY the information from RAW_CONTENT that is relevant to TASK. "
    "Be concise and factual. Preserve exact code, numbers, names, and errors verbatim. "
    "Discard anything irrelevant to TASK. Do not add commentary, opinions, or explain what you did. "
    "If nothing is relevant, output exactly: NO_RELEVANT_CONTENT."
)


def check_ollama_running(model):
    try:
        with urllib.request.urlopen(f"{OLLAMA_HOST}/api/tags", timeout=3) as resp:
            data = json.loads(resp.read())
    except urllib.error.URLError:
        sys.exit(f"error: Ollama not reachable at {OLLAMA_HOST}. Start it with `ollama serve`.")
    names = {m.get("name") for m in data.get("models", [])}
    if model not in names:
        sys.exit(f"error: model '{model}' not pulled. Run `ollama pull {model}` (available: {', '.join(sorted(names)) or 'none'}).")


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


MAX_CONTENT_CHARS = 24000  # ~6k tokens; keeps prompt+content under num_ctx below with room for output
NUM_CTX = 8192


def call_ollama(model, task, content, max_words):
    if len(content) > MAX_CONTENT_CHARS:
        print(
            f"warning: input is {len(content)} chars, truncating to {MAX_CONTENT_CHARS} "
            f"to fit the model's context window; split large directories into smaller runs",
            file=sys.stderr,
        )
        content = content[:MAX_CONTENT_CHARS]
    prompt = (
        f"{SYSTEM_PROMPT}\n\n"
        f"TASK: {task}\n\n"
        f"Keep the output under {max_words} words.\n\n"
        f"RAW_CONTENT:\n{content}"
    )
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"num_ctx": NUM_CTX, "temperature": 0.2},
    }).encode("utf-8")
    req = urllib.request.Request(
        OLLAMA_URL, data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
    except urllib.error.URLError as e:
        sys.exit(f"error: cannot reach Ollama at {OLLAMA_URL} ({e}). Is `ollama serve` running?")
    return data.get("response", "").strip()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--task", help="what the filtered content will be used for (required unless --grep is used alone)")
    ap.add_argument("--input", help="file or directory to filter/search (recursive); omit to read stdin")
    ap.add_argument("--grep", metavar="PATTERN", help="exact regex search (like grep -rn) under --input, no LLM")
    ap.add_argument("--ignore-case", action="store_true", help="case-insensitive --grep")
    ap.add_argument("--model", default="qwen2.5:7b", help="Ollama model tag (default: qwen2.5:7b)")
    ap.add_argument("--max-words", type=int, default=300, help="target max words of output (default: 300)")
    args = ap.parse_args()

    if args.grep:
        root = confine_to_root(args.input) if args.input else ROOT
        matches = grep_search(root, args.grep, args.ignore_case)
        if not matches:
            print("NO_MATCHES")
            return
        if not args.task:
            print("\n".join(matches))
            return
        check_ollama_running(args.model)
        result = call_ollama(args.model, args.task, "\n".join(matches), args.max_words)
        print(result)
        return

    if not args.task:
        ap.error("--task is required unless --grep is used")
    check_ollama_running(args.model)
    content = read_input(args.input)
    result = call_ollama(args.model, args.task, content, args.max_words)
    print(result)


if __name__ == "__main__":
    main()
