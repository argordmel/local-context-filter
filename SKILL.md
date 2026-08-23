---
name: local-context-filter
description: Use when you need to feed a large file, log dump, or raw text blob into a Claude conversation but only part of it is relevant — filters/summarizes it through a local Ollama model first so Claude receives only what matters, saving context tokens. Also use for an exact recursive text/regex search (grep-style) across the current project without any of it entering Claude's context.
---

# Local Context Filter

## Overview

Pipes raw content (files, logs, pasted text) through a local Ollama model
(`qwen2.5:7b` default) with a task description. The local model strips
everything irrelevant and returns a compact result — that's what goes into
Claude's context, not the raw blob.

## When to Use

- Reading a large log file / error dump where only a few lines matter
- A long doc/spec where only one section is relevant to the current task
- Any raw text (paste, scrape, big JSON) where forwarding it whole would
  burn context on noise
- Exact pattern search across a whole project (`--grep`, see below) —
  cheaper and more precise than the LLM-filter mode for this

**Don't use** the LLM-filter mode (`--task` without `--grep`) for content
that's already small, or when you need the exact full content verbatim
(the local model can paraphrase/drop things you didn't expect — fine for
logs/docs, riskier for code you'll edit). For exact-match search use
`--grep` instead, not `--task`.

## Requirements

- `ollama serve` running locally (`curl -s http://localhost:11434/api/tags`
  to check)
- Model pulled: `ollama pull qwen2.5:7b` (or pass `--model` for another tag)

The script self-checks both before doing any work: it fails fast with
`error: Ollama not reachable at http://localhost:11434. Start it with
\`ollama serve\`.` if the server is down, or `error: model 'X' not pulled.
Run \`ollama pull X\` (available: ...)` if the tag isn't present locally —
before reading `--input` or making any generate call.

## Usage

```bash
python3 ~/.claude/skills/local-context-filter/filter.py \
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
| `--model` | `qwen2.5:7b` | Ollama model tag |
| `--max-words` | 300 | target size of filtered output |

## Common Mistakes

- Ollama down or model not pulled — script now catches this upfront and
  tells you the exact fix command, no need to debug it yourself.
- Vague `--task` ("summarize this") — the filter quality depends entirely
  on task specificity. "find the auth-related error" beats "look at this".
- Very large directories — content over ~24k chars gets truncated (warning
  on stderr) to fit the model's context window; split big directory scans
  into smaller subfolder runs instead of one pass over everything.
- Using it on code you intend to edit — prefer reading the real file
  directly when exact content/formatting matters.
