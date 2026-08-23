---
name: local-context-filter
description: Use when you need to feed a large file, log dump, or raw text blob into a Claude conversation but only part of it is relevant — filters/summarizes it through a local model (Ollama, LM Studio, or any OpenAI-compatible server) first so Claude receives only what matters, saving context tokens. Also use for an exact recursive text/regex search (grep-style, `--grep`), to list a project's file/directory tree (`--ls`), to find a file by name (glob, `--find`), to count lines per file (`--count`), to summarize commit history (`--log`), or to run an npm/npx/pnpm/yarn command (`--run`), all without any of it entering Claude's context.
---

# Local Context Filter

## Overview

Pipes raw content (files, logs, pasted text) through a local model — Ollama
(default, `qwen2.5:7b`), LM Studio, or any other OpenAI-compatible server
(llama.cpp server, vLLM, etc. via `--backend openai`) — with a task
description. The local model strips everything irrelevant and returns a
compact result — that's what goes into Claude's context, not the raw blob.

## When to Use

- Reading a large log file / error dump where only a few lines matter
- A long doc/spec where only one section is relevant to the current task
- Any raw text (paste, scrape, big JSON) where forwarding it whole would
  burn context on noise
- Exact pattern search across a whole project (`--grep`, see below) —
  cheaper and more precise than the LLM-filter mode for this
- Reviewing/summarizing the current working tree changes (`--diff`, see
  below) without pasting a large `git diff` into the conversation
- Reviewing/summarizing commit history (`--log`, see below) without
  pasting a large `git log` into the conversation
- Listing a directory tree (`--ls`, see below) — use this instead of
  `bash ls`/`tree` so path exploration also costs zero Claude tokens
- Finding a file by name (`--find`, see below) — use this instead of
  `bash find`/`find -iname` for the same reason
- Counting lines in a file/directory (`--count`, see below) — use this
  instead of `bash wc -l` for the same reason
- Running an `npm`/`npx`/`pnpm`/`yarn` command and filtering noisy install
  output down to real errors (`--run`, see below)

**Don't use** the LLM-filter mode (`--task` without `--grep`) for content
that's already small, or when you need the exact full content verbatim
(the local model can paraphrase/drop things you didn't expect — fine for
logs/docs, riskier for code you'll edit). For exact-match search use
`--grep` instead, not `--task`.

## Fallback when no backend is reachable

If `--task` (LLM-filter) mode errors with `error: ollama not reachable...`
or `error: lmstudio not reachable...`, don't retry the same command or try
the other `--backend` blind — neither is confirmed running. Fall back
immediately to reading the file(s)/directory directly with built-in
Read/Grep tools instead, and tell the user in one line that no local model
was available so this ran through Claude's own context.

`--grep` mode never needs this — it doesn't touch either backend, so it
keeps working regardless of whether Ollama or LM Studio is running.

## Requirements

Nothing auto-starts a service — not Ollama, not LM Studio. The script never
launches a backend for you; it only checks reachability and fails with a
clear error if the one you selected isn't running. If `--task` mode fails
because no backend is up, don't try to start one yourself — see
[Fallback](#fallback-when-no-backend-is-reachable) above.

Pick one backend — both aren't meant to run at once (RAM):

- **Ollama** (default): `ollama serve` running locally
  (`curl -s http://localhost:11434/api/tags` to check). Without `--model`,
  it auto-picks whatever model is currently loaded in Ollama's memory
  (`/api/ps`) — matches whatever you're already running, no extra load
  time. Falls back to `qwen2.5:7b` if pulled, else the alphabetically
  first pulled model, if nothing is currently loaded. Pass `--model` to
  override.
- **LM Studio**: local server started (Developer tab > Start Server,
  default `http://localhost:1234`) with a model loaded. Pass
  `--backend lmstudio`; without `--model`, it auto-picks whatever model
  is currently loaded (via LM Studio's `/api/v0/models`, which exposes
  load state — plain `/v1/models` lists every downloaded model regardless
  of load state, so it can't be used for this).
- **Any other OpenAI-compatible server** (llama.cpp server, vLLM, etc.):
  `--backend openai --host http://host:port` — `--host` is required, no
  conventional default port to assume. `--model` optional (defaults to
  the first model the server's `/v1/models` reports).

The script self-checks before doing any work: it fails fast with
`error: {backend} not reachable at {host}. Start it with ...` if the
server is down, or `error: model 'X' not available on {backend}. ...` if
the tag/id isn't present — before reading `--input` or making any call.

Override the host with `--host` if a backend runs on a non-default port.

## Usage

```bash
# Ollama (default)
python3 ~/.claude/skills/local-context-filter/filter.py \
  --task "what you're trying to accomplish" \
  --input /path/to/file.log \
  --max-words 300

# LM Studio
python3 ~/.claude/skills/local-context-filter/filter.py \
  --backend lmstudio \
  --task "what you're trying to accomplish" \
  --input /path/to/file.log \
  --max-words 300

# any other OpenAI-compatible server (llama.cpp, vLLM, ...)
python3 ~/.claude/skills/local-context-filter/filter.py \
  --backend openai --host http://localhost:8080 \
  --task "what you're trying to accomplish" \
  --input /path/to/file.log \
  --max-words 300
```

`--input` may be a file or a directory — directories are searched
**recursively into subdirectories**. A **relative** `--input` (the normal
case) is confined to the current working directory: it will never read
anything above the folder you ran it from (`../` is rejected). An
**absolute** or **`~`-expanded** `--input` (e.g.
`--input /Users/you/other-project` or `--input ~/other-project`) is also
allowed, but only when it resolves inside your home directory — that path
then becomes its own trust boundary for the rest of that command, so you
can point at a different project without `cd`ing into it first. Either
way, symlinks pointing outside the active boundary are rejected —
including one found *inside* the tree partway through a recursive walk,
not just in `--input` itself.

A `--input` that doesn't exist on disk at all is an error (`error: 'X'
does not exist`) for `--grep`/`--find`/`--count` — it's not conflated with
a real "nothing matched" result (`NO_MATCHES`/`NO_ENTRIES`), which means
the path did exist and was legitimately empty/had no matches.

Or pipe stdin:

```bash
cat big.log | python3 ~/.claude/skills/local-context-filter/filter.py --task "find the root cause of the timeout"
```

Prints only the filtered/relevant text to stdout — read that output, not
the original file, into the conversation.

### Exact search (`--grep`) — no LLM, no context cost

```bash
python3 ~/.claude/skills/local-context-filter/filter.py --grep "ServiceOrder"
```

Real recursive regex search (like `grep -rn`), rooted at `--input` (default:
cwd — the whole current project, subdirs included), same confinement rules
as above. `--input` can also point to a single file, in which case only
that file is searched. Auto-skips `.git`, `node_modules`, `dist`, `build`, `.venv`,
`__pycache__`, `.next`, `coverage`. Caps at 500 matches (warns on stderr and
tells you to narrow the pattern or `--input` path if hit). Prints exact
`path:line: content` — never touches Ollama, so this is the cheapest and
most precise option; prefer it over `--task` whenever you know the literal
string/regex you're looking for.

Add `--ignore-case` for case-insensitive matching.

Combine with `--task` when a plain grep returns too much noise and you want
the local model to narrow the match list down to what's relevant:

```bash
python3 ~/.claude/skills/local-context-filter/filter.py --grep "TODO" --task "which TODOs are about auth"
```

**Trust, but verify: `--grep --task` prints two extra signals after the
model's answer.** A `Confidence: high|medium|low — <reason>` line is the
model's own self-assessment — useful, but not fully trustworthy on its
own (local models have been observed claiming "high" while dropping a
directly relevant file). A `coverage: N/M source files referenced in this
summary (missing: ...)` line is computed by the script itself, not the
model — it's an objective count of how many distinct files from the raw
grep matches actually got mentioned in the filtered output. When coverage
is incomplete, don't take the summary at face value: re-run `--grep`
without `--task` (or read the missing files directly) before treating the
answer as settled, especially for anything beyond straightforward search/
navigation/diff-summary use — for real architecture decisions, look at
the evidence yourself rather than trusting a local model's synthesis of it.

### Working-tree diff (`--diff`)

```bash
# raw diff, whole repo, no LLM
python3 ~/.claude/skills/local-context-filter/filter.py --diff

# scoped to one file/directory
python3 ~/.claude/skills/local-context-filter/filter.py --diff --input README.md

# filtered by task
python3 ~/.claude/skills/local-context-filter/filter.py --diff --task "which changes touch auth"
```

Runs `git diff HEAD` (staged + unstaged) at cwd and prints it as-is — no LLM
involved, zero context cost, same as `--grep` alone. Pass `--input` (a file
or directory, same confinement rules as elsewhere) to scope the diff to
just that path instead of the whole repo. Add `--task` to have the local
model filter the diff down to what's relevant. Prints `NO_CHANGES` if the
diff is empty, exits with an error if cwd isn't a git repo. Cannot combine
with `--grep`.

### Commit history (`--log`)

```bash
# last 50 commits, whole repo, no LLM
python3 ~/.claude/skills/local-context-filter/filter.py --log

# scoped to one file/directory, fewer commits
python3 ~/.claude/skills/local-context-filter/filter.py --log --limit 10 --input README.md

# summarized by task
python3 ~/.claude/skills/local-context-filter/filter.py --log --task "what changed around auth recently"
```

Runs `git log` (most recent `--limit` commits, default 50) at cwd and prints
it as-is — no LLM involved, zero context cost, same as `--diff` alone. Pass
`--input` (a file or directory, same confinement rules as elsewhere) to scope
the log to just that path's history instead of the whole repo. Add `--task`
to have the local model summarize it down to what's relevant. Prints
`NO_COMMITS` if the log is empty, exits with an error if cwd isn't a git
repo. Cannot combine with `--grep`/`--diff`/`--ls`/`--find`/`--run`/`--count`.

### Directory listing (`--ls`) — no LLM, no context cost

```bash
python3 ~/.claude/skills/local-context-filter/filter.py --ls
python3 ~/.claude/skills/local-context-filter/filter.py --ls --input app/config
```

Recursively lists files and directories rooted at `--input` (default: cwd),
same confinement and exclusion rules as `--grep`. Directories print with a
trailing `/`. Prints `NO_ENTRIES` if the directory is empty. Use this
instead of `bash ls`/`find`/`bash tree` for exploring project structure or
locating a file by name — same zero-Claude-token principle as `--grep`.
Cannot combine with `--grep` or `--diff`.

Add `--task` to have the local model narrow a large listing down to what's
relevant:

```bash
python3 ~/.claude/skills/local-context-filter/filter.py --ls --input app --task "which files look auth-related"
```

### Filename search (`--find`) — no LLM, no context cost

```bash
python3 ~/.claude/skills/local-context-filter/filter.py --find "*.log"
python3 ~/.claude/skills/local-context-filter/filter.py --find "README*" --ignore-case
```

Finds files/dirs whose **basename** matches a glob pattern — like
`find -iname`, not a content search (use `--grep` for content). Rooted at
`--input` (default: cwd), same confinement/exclusion rules as `--grep`/
`--ls`; `--input` can also point to a single file, in which case just that
file's own basename is tested. Directories print with a trailing `/`. Prints `NO_MATCHES` if
nothing matches, caps at 500 like `--grep`. Add `--ignore-case` for
case-insensitive matching, `--task` to have the local model narrow a large
match list. Mutually exclusive with `--grep`/`--diff`/`--log`/`--ls`.

### Line counting (`--count`) — no LLM, no context cost

```bash
python3 ~/.claude/skills/local-context-filter/filter.py --count --input filter.py
python3 ~/.claude/skills/local-context-filter/filter.py --count
```

Counts lines per file, like `wc -l` — counts newline characters, exactly
matching real `wc -l` even on a file with no trailing newline (`N-1`, not
`N`). `--input` a single file prints one `N path` line; `--input` a
directory (default: cwd) recurses, same confinement/exclusion rules as
`--grep`/`--ls`/`--find`, and appends an `N TOTAL` line. No file content is included in the output, only counts.
Prints `NO_ENTRIES` if the directory has no readable text files. Add
`--task` to have the local model narrow a large listing down to what's
relevant. Mutually exclusive with `--grep`/`--diff`/`--log`/`--ls`/`--find`/`--run`.

### Package manager commands (`--run`)

```bash
python3 ~/.claude/skills/local-context-filter/filter.py --run "npm install"
python3 ~/.claude/skills/local-context-filter/filter.py --run "pnpm install" --input packages/app
python3 ~/.claude/skills/local-context-filter/filter.py --run "yarn install" --task "which errors are blocking install"
```

Runs an `npm`/`npx`/`pnpm`/`yarn` command — only these four binaries are
allowed, anything else is rejected before it executes — at `--input`
(default: cwd, must be a directory). Same confinement rules as elsewhere. Prints combined
stdout+stderr as-is, no LLM involved, same zero-context-cost principle as
`--diff`. Add `--task` to have the local model filter a noisy install log
down to the actual errors/warnings that matter. Raw output (no `--task`)
over ~24k chars is truncated with a stderr warning, same budget as the
LLM-filter path — add `--task` instead of relying on the raw dump for a
very noisy install. Prints `NO_OUTPUT` if the command produced nothing.
10-minute timeout. Mutually exclusive with `--grep`/`--diff`/`--log`/`--ls`/`--find`/`--count`.

**Security note:** the allowlist and directory confinement stop `--run`
from being a general command runner, but they don't stop what `npm`/
`yarn`/`pnpm` themselves can do once invoked — `install`/postinstall
scripts run arbitrary code from the package (same as running that
install by hand), and the subprocess inherits the full parent
environment, including any secrets in it (API keys, tokens). Only use
`--run` against `package.json`s you trust, same as you would running
`npm install` yourself directly.

### Project-level excludes (`.claude/local-context-filter.json`)

`--grep`, `--ls`, `--find`, and `--count` skip `.git`, `node_modules`, `dist`,
`build`, `.venv`, `__pycache__`, `.next`, `coverage` by default. To skip
more directories for one project without touching the skill itself, drop
a JSON file at `<project root>/.claude/local-context-filter.json`:

```json
{"exclude": ["fixtures", "vendor"]}
```

Read fresh on every run, from the directory you ran the command from —
no flag needed once it's there.

### Real cost example

Measured on a mid-size project, searching for `ServiceOrder` under `app/`
(55 matches):

| Method | Claude tokens spent | Accuracy |
|---|---|---|
| Reading matching files directly / built-in grep output pasted into context | ~1485 tokens (5940 chars) | exact |
| `--grep "ServiceOrder" --input app` | **0 tokens** | exact, identical matches |

Same 55 matches, same line numbers, same content — the only difference is
whether that text ever entered Claude's context. For exact-pattern search,
`--grep` wins outright: no accuracy tradeoff, full token savings.

## Flags

| Flag | Default | Purpose |
|---|---|---|
| `--task` | required unless `--grep` used alone | what you're using this content for — steers what's kept |
| `--input` | stdin (LLM mode) / cwd (`--grep`, `--diff`, `--log`) | file or directory to filter/search/diff/log |
| `--grep` | — | exact regex pattern; switches to no-LLM search mode |
| `--ignore-case` | off | case-insensitive `--grep` |
| `--diff` | off | filter `git diff HEAD` at cwd, or scoped to `--input` if given; no-LLM without `--task` |
| `--log` | off | filter `git log` at cwd, or scoped to `--input` if given; no-LLM without `--task` |
| `--limit` | 50 | max commits for `--log` |
| `--ls` | off | recursively list files/dirs under `--input` (default: cwd); no-LLM without `--task` |
| `--find` | — | glob pattern for basename search (like `find -iname`); no-LLM without `--task` |
| `--count` | off | count lines per file under `--input` (like `wc -l`); no-LLM without `--task` |
| `--run` | — | run an `npm`/`npx`/`pnpm`/`yarn` command; no-LLM without `--task` |
| `--report` | off | print a usage.json summary (total runs, tokens saved by mode); cannot combine with anything else |
| `--clean` | off | delete usage.json; cannot combine with anything else |
| `--backend` | `ollama` | local LLM server: `ollama`, `lmstudio`, or `openai` (generic) |
| `--host` | backend default | override host (ollama `:11434`, lmstudio `:1234`); **required** for `--backend openai` |
| `--model` | ollama: currently loaded model, else `qwen2.5:7b`, else first pulled / lmstudio, openai: first available model | model tag/id |
| `--max-words` | 300 | target size of filtered output (applies per-chunk when content is split, see below) |

## Usage log (local, private)

Every run appends one line to `usage.json` next to `filter.py` (gitignored,
never leaves the machine): `{ts, mode, backend, chars_in, chars_out,
tokens_saved_est}`. No file contents, paths, or `--task` text are recorded
— only counts, so it's safe to leave on and cheap to `tail` or delete.
`tokens_saved_est` is a rough `chars/4` proxy, not the real Claude
tokenizer — good for trend, not exact accounting. Override the location
with `LOCAL_CONTEXT_FILTER_LOG=/path/to/file`; a write failure (e.g.
read-only disk) is silently ignored and never breaks the actual command.
A corrupted or badly-encoded log (e.g. `LOCAL_CONTEXT_FILTER_LOG` pointed
at a non-text file) is handled the same way — `--report` prints
`NO_USAGE_DATA` instead of crashing.

Self-maintaining: once the log passes 6000 lines it's automatically
trimmed down to the most recent 5000, so it never grows unbounded — no
manual `--clean` needed for upkeep. `--clean` still exists for when you
want to reset the count to zero on purpose.

Two flags read/manage it directly, no LLM involved:

```bash
python3 ~/.claude/skills/local-context-filter/filter.py --report
python3 ~/.claude/skills/local-context-filter/filter.py --clean
```

`--report` prints total runs and estimated tokens saved, broken down by
mode; prints `NO_USAGE_DATA` if the log is empty/missing. `--clean`
deletes the log file (prints `USAGE_LOG_CLEARED`, or `NO_USAGE_DATA` if
there was nothing to delete) — use it to reset the count or just to keep
the file from growing unbounded.

## Common Mistakes

- Backend down or model not available — script now catches this upfront
  and tells you the exact fix command, no need to debug it yourself.
- Running Ollama and LM Studio at the same time on a low-RAM machine —
  stop one before starting the other.
- Vague `--task` ("summarize this") — the filter quality depends entirely
  on task specificity. "find the auth-related error" beats "look at this".
- Very large content (over ~24k chars) — automatically split into
  sequential chunks and each filtered separately (a stderr warning names
  the chunk count), so nothing is silently dropped; it's just several LLM
  calls instead of one, so it takes longer. No action needed, but expect
  the wait on huge directories/diffs.
- Using it on code you intend to edit — prefer reading the real file
  directly when exact content/formatting matters.
