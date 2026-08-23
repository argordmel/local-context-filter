# Skill: Local Context Filter

A [Claude Code](https://claude.com/claude-code) skill (`local-context-filter`) that
offloads big/raw context to a **local** LLM before anything reaches Claude:

- **`--grep`** — real recursive regex search (like `grep -rn`), rooted at the
  current directory, never escaping upward. No LLM involved, zero Claude
  tokens spent — this is the cheapest and most precise option whenever you
  know the literal string/pattern you're looking for.
- **`--task`** (LLM-filter mode) — pipes a file/directory/stdin through a
  local model with a task description; the model strips everything
  irrelevant and only the compact result goes into Claude's context.
- **`--grep` + `--task`** — grep first (exact, free), then have the local
  model narrow a noisy match list down to what's relevant.
- **`--diff`** — filters `git diff HEAD` at cwd; raw diff (free) without
  `--task`, or model-filtered with it.

Backed by [Ollama](https://ollama.com) (default), [LM Studio](https://lmstudio.ai),
or any other OpenAI-compatible server (llama.cpp server, vLLM, ...) via
`--backend openai --host <url>`. Selectable via `--backend`. See [TODO](#todo).

## Why

Reading a big log file, a whole directory, or a large doc into a Claude
conversation burns context tokens on noise. This skill does that filtering
locally and for free — Claude only ever sees the distilled result.

## Install

```bash
git clone git@github.com:argordmel/local-context-filter.git ~/.claude/skills/local-context-filter
```

That's it — Claude Code auto-discovers any skill under `~/.claude/skills/`.
No further registration step.

If you already have a `~/.claude/skills/local-context-filter` (e.g. from an
earlier manual setup), remove it first or clone elsewhere and symlink:

```bash
git clone git@github.com:argordmel/local-context-filter.git ~/code/local-context-filter
ln -s ~/code/local-context-filter ~/.claude/skills/local-context-filter
```

### Requirements

- Python 3 (stdlib only — no dependencies to install)
- `--grep` alone needs neither backend, it's pure Python.
- For `--task` (LLM-filter) mode, one of:
  - **Ollama** (default): `ollama serve` running locally, model pulled,
    e.g. `ollama pull qwen2.5:7b`.
  - **LM Studio**: local server started (Developer tab > Start Server,
    default port 1234) with a model loaded; pass `--backend lmstudio`.
  - **Any other OpenAI-compatible server** (llama.cpp server, vLLM, ...):
    `--backend openai --host http://host:port` — `--host` is required,
    there's no conventional default port to assume.

Only one backend needs to run at a time — pick one with `--backend`.

## Usage

```bash
# exact search, whole current project, no LLM, no Claude tokens
python3 ~/.claude/skills/local-context-filter/filter.py --grep "ServiceOrder"

# same, case-insensitive, scoped to a subfolder
python3 ~/.claude/skills/local-context-filter/filter.py --grep "todo" --ignore-case --input app

# LLM-filter a big file/dir down to what matters (Ollama, default)
python3 ~/.claude/skills/local-context-filter/filter.py \
  --task "find the root cause of the timeout" --input server.log --max-words 200

# same, via LM Studio
python3 ~/.claude/skills/local-context-filter/filter.py \
  --backend lmstudio \
  --task "find the root cause of the timeout" --input server.log --max-words 200

# same, via any other OpenAI-compatible server
python3 ~/.claude/skills/local-context-filter/filter.py \
  --backend openai --host http://localhost:8080 \
  --task "find the root cause of the timeout" --input server.log --max-words 200

# grep first, then let the local model narrow a noisy match list
python3 ~/.claude/skills/local-context-filter/filter.py --grep "TODO" --task "which TODOs are about auth"

# raw working-tree diff, no LLM, no Claude tokens
python3 ~/.claude/skills/local-context-filter/filter.py --diff

# same, filtered by task
python3 ~/.claude/skills/local-context-filter/filter.py --diff --task "which changes touch auth"
```

Full flag reference and behavior notes: [SKILL.md](SKILL.md).

## Activating it by default

Skills in `~/.claude/skills/` are **global** — available in every project,
automatically, as soon as they're installed. Claude Code reads each skill's
`description` frontmatter and decides on its own when a skill is relevant;
there's no separate "enable" step and nothing project-specific to configure.

To make Claude actually **prefer** this over its own built-in file-reading/
grep tools for exact-pattern searches (rather than just having it available),
add a line like this to your global `~/.claude/CLAUDE.md` (applies to every
project) or a project's own `CLAUDE.md`:

```markdown
# Local search
For exact string/regex search in this repo, prefer the `local-context-filter`
skill's `--grep` mode over reading files directly — it costs zero context
tokens. Only fall back to reading files when you need to see full file
content, not just matching lines.
```

Without that instruction, Claude will still *discover* the skill via its
description when a task looks like a fit — the CLAUDE.md line just makes the
preference explicit and consistent instead of judgment-call-by-judgment-call.

## Safety

- Path confinement: `--input` (and `--grep`'s search root) can go into
  subdirectories freely but can never resolve above the directory you ran
  the command from — `../`, absolute paths outside it, and symlinks
  pointing out are all rejected.
- `--grep` auto-skips `.git`, `node_modules`, `dist`, `build`, `.venv`,
  `__pycache__`, `.next`, `coverage`, and caps at 500 matches.
- The LLM-filter mode truncates input over ~24k chars (with a stderr
  warning) to stay inside the model's context window — split large
  directory scans into smaller runs instead of one pass over everything.

## Tests

```bash
python3 ~/.claude/skills/local-context-filter/test_filter.py -v
```

`unittest`, stdlib only. Mocks all network calls (Ollama/LM Studio/generic
OpenAI-compatible) and uses real temp git repos for `--diff` — no server
needs to be running to pass.

## TODO

- [x] Support [LM Studio](https://lmstudio.ai) (OpenAI-compatible local
      server) as an alternative backend to Ollama.
- [x] Support other local runtimes generically (llama.cpp server, vLLM, any
      OpenAI-compatible `/v1/chat/completions` endpoint) via `--backend openai`
      + `--host`.

## License

MIT
