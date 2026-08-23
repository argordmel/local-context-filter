# local-context-filter

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

Currently backed by [Ollama](https://ollama.com). See [TODO](#todo).

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
- [Ollama](https://ollama.com) running locally for the `--task` (LLM-filter)
  mode: `ollama serve`, and a model pulled, e.g. `ollama pull qwen2.5:7b`
  — `--grep` alone needs neither, it's pure Python.

## Usage

```bash
# exact search, whole current project, no LLM, no Claude tokens
python3 ~/.claude/skills/local-context-filter/filter.py --grep "ServiceOrder"

# same, case-insensitive, scoped to a subfolder
python3 ~/.claude/skills/local-context-filter/filter.py --grep "todo" --ignore-case --input app

# LLM-filter a big file/dir down to what matters
python3 ~/.claude/skills/local-context-filter/filter.py \
  --task "find the root cause of the timeout" --input server.log --max-words 200

# grep first, then let the local model narrow a noisy match list
python3 ~/.claude/skills/local-context-filter/filter.py --grep "TODO" --task "which TODOs are about auth"
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

## TODO

- [ ] Support [LM Studio](https://lmstudio.ai) (OpenAI-compatible local
      server) as an alternative backend to Ollama.
- [ ] Support other local runtimes generically (llama.cpp server, vLLM, any
      OpenAI-compatible `/v1/chat/completions` endpoint) via a `--backend`
      flag instead of hardcoding Ollama's `/api/generate`.

## License

MIT
