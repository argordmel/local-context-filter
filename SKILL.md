---
name: local-context-filter
description: Use when you need to feed a large file, log dump, or raw text blob into a Claude conversation but only part of it is relevant — filters/summarizes it through a local model (Ollama, LM Studio, or any OpenAI-compatible server) first so Claude receives only what matters, saving context tokens. Also use for an exact recursive text/regex search (grep-style) across the current project without any of it entering Claude's context.
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
  (`curl -s http://localhost:11434/api/tags` to check). Model pulled:
  `ollama pull qwen2.5:7b` (or pass `--model` for another tag).
- **LM Studio**: local server started (Developer tab > Start Server,
  default `http://localhost:1234`) with a model loaded. Pass
  `--backend lmstudio`; `--model` optional (defaults to the first loaded
  model).
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
**recursively into subdirectories**, but the script is confined to the
current working directory: it will never read anything above the folder
you ran it from (`../`, absolute paths outside cwd, symlinks pointing out
are all rejected). Run it from the folder you want as the search root.

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
as above. Auto-skips `.git`, `node_modules`, `dist`, `build`, `.venv`,
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

### Working-tree diff (`--diff`)

```bash
# raw diff, no LLM
python3 ~/.claude/skills/local-context-filter/filter.py --diff

# filtered by task
python3 ~/.claude/skills/local-context-filter/filter.py --diff --task "which changes touch auth"
```

Runs `git diff HEAD` (staged + unstaged) at cwd and prints it as-is — no LLM
involved, zero context cost, same as `--grep` alone. Add `--task` to have the
local model filter the diff down to what's relevant. Prints `NO_CHANGES` if
the working tree is clean, exits with an error if cwd isn't a git repo.
Cannot combine with `--grep` or `--input`.

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
| `--input` | stdin (LLM mode) / cwd (`--grep`) | file or directory to filter/search |
| `--grep` | — | exact regex pattern; switches to no-LLM search mode |
| `--ignore-case` | off | case-insensitive `--grep` |
| `--diff` | off | filter `git diff HEAD` at cwd instead of `--input`; no-LLM without `--task` |
| `--backend` | `ollama` | local LLM server: `ollama`, `lmstudio`, or `openai` (generic) |
| `--host` | backend default | override host (ollama `:11434`, lmstudio `:1234`); **required** for `--backend openai` |
| `--model` | `qwen2.5:7b` (ollama) / first available model (lmstudio, openai) | model tag/id |
| `--max-words` | 300 | target size of filtered output |

## Common Mistakes

- Backend down or model not available — script now catches this upfront
  and tells you the exact fix command, no need to debug it yourself.
- Running Ollama and LM Studio at the same time on a low-RAM machine —
  stop one before starting the other.
- Vague `--task` ("summarize this") — the filter quality depends entirely
  on task specificity. "find the auth-related error" beats "look at this".
- Very large directories — content over ~24k chars gets truncated (warning
  on stderr) to fit the model's context window; split big directory scans
  into smaller subfolder runs instead of one pass over everything.
- Using it on code you intend to edit — prefer reading the real file
  directly when exact content/formatting matters.
