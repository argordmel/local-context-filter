# Skill: Local Context Filter

*[Leer en español](README.es.md)*

A skill for [Claude Code](https://claude.com/claude-code) (`local-context-filter`) that offloads big/raw context to a **local** LLM before anything reaches Claude:

- **`--grep`** — real recursive regex search (like `grep -rn`), rooted at the
  current directory, never escaping upward. No LLM involved, zero Claude
  tokens spent — this is the cheapest and most precise option whenever you
  know the literal string/pattern you're looking for.
- **`--task`** (LLM-filter mode) — pipes a file/directory/stdin through a
  local model with a task description; the model strips everything
  irrelevant and only the compact result goes into Claude's context.
- **`--grep` + `--task`** — grep first (exact, free), then have the local
  model narrow a noisy match list down to what's relevant.
- **`--diff`** — filters `git diff HEAD` at cwd (or scoped to `--input`);
  raw diff (free) without `--task`, or model-filtered with it.

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

**No hardcoded default model for LM Studio/openai.** Only Ollama has one
(`qwen2.5:7b`). Without `--model`, LM Studio and openai always pick
whichever model sorts first alphabetically among what `/v1/models`
reports — not a "preferred" model, just alphabetical order on whatever
happens to be loaded on your machine. Pass `--model` explicitly if you
want a specific one.

Only one backend needs to run at a time — pick one with `--backend`.

**Nothing auto-starts a service for you — not Ollama, not LM Studio.**
Starting/stopping the backend is on you; the script only checks whether
it's reachable and fails with a clear error (and a fix command) if it
isn't. Claude doesn't warn you beforehand either — it just runs the
command and only finds out from that error if the backend is down.

**Telling Claude what's running:** just say so in chat, plain language —
e.g. *"I have Ollama running"* or *"LM Studio is up"*. No special syntax
needed; Claude passes the right `--backend` on the commands it runs for
the rest of that conversation. Without a model named, it picks whatever
loads first (see above) — mention a specific model only if you want that
one used instead. Say it again in a future session if you want it
remembered there too — it isn't persisted automatically.

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

# raw working-tree diff, whole repo, no LLM, no Claude tokens
python3 ~/.claude/skills/local-context-filter/filter.py --diff

# same, scoped to one file
python3 ~/.claude/skills/local-context-filter/filter.py --diff --input README.md

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
For exact string/regex search in a project, prefer the `local-context-filter` skill's `--grep` mode over reading files directly or built-in grep tools — it costs zero Claude context tokens. Only fall back to reading files when full file content/formatting is needed, not just matching lines.

For reviewing/summarizing the current working tree changes, prefer the same skill's `--diff` mode (`git diff HEAD` under the hood) over running `git diff` and pasting its output into context — `--diff` alone (no `--task`) is free too. Add `--task` only when the raw diff is too noisy and needs filtering by a local model.
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

### Coverage checklist

| Feature | Test(s) |
|---|---|
| `--grep` exact search, line numbers | `TestGrepSearch.test_finds_matches_with_line_numbers`, `TestCLIEndToEnd.test_grep_prints_matches` |
| `--grep --ignore-case` | `TestGrepSearch.test_case_insensitive`, `TestCLIEndToEnd.test_grep_ignore_case_flag` |
| `--grep` no matches → `NO_MATCHES` | `TestGrepSearch.test_no_matches_returns_empty_list`, `TestCLIEndToEnd.test_grep_no_matches_prints_sentinel` |
| `--grep` skips `.git`/`node_modules`/etc. | `TestGrepSearch.test_skips_excluded_dirs` |
| `--grep` 500-match cap + warning | `TestGrepSearch.test_hits_match_cap_and_warns` |
| `--grep` skips unreadable/binary files | `TestGrepSearch.test_binary_file_skipped_without_crash` |
| `--diff` whole repo, raw | `TestGitDiff.test_unstaged_change_shows_in_diff`, `TestGitDiff.test_staged_change_shows_in_diff`, `TestCLIEndToEnd.test_diff_prints_raw_diff` |
| `--diff --input` scoped to one path | `TestGitDiff.test_path_scopes_diff_to_single_file`, `TestCLIEndToEnd.test_diff_scoped_to_input` |
| `--diff` clean tree → `NO_CHANGES` | `TestGitDiff.test_no_changes_returns_empty_string`, `TestCLIEndToEnd.test_diff_no_changes_prints_sentinel` |
| `--diff` outside a git repo → error | `TestGitDiff.test_not_a_git_repo_errors`, `TestCLIEndToEnd.test_diff_not_a_git_repo_errors` |
| `--diff` + `--grep` mutually exclusive | `TestCLIArgGating.test_diff_and_grep_mutually_exclusive` |
| Path confinement (`../`, absolute, symlink escape) | `TestConfineToRoot` (all cases, incl. `test_symlink_pointing_outside_root_rejected`) |
| `--task` reading a file / directory / stdin | `TestReadInput`, `TestReadDirectory` |
| `--task` with no `--input` and no stdin → error | `TestReadInput.test_no_input_and_no_stdin_exits` |
| Ollama backend: list models, generate call | `TestListModels.test_ollama_parses_names`, `TestCallLLM.test_ollama_returns_response_field` |
| LM Studio backend: list models, chat completions | `TestListModels.test_lmstudio_parses_ids`, `TestCallLLM.test_lmstudio_returns_message_content` |
| Generic `openai` backend | `TestListModels.test_openai_parses_ids_same_shape_as_lmstudio`, `TestCallLLM.test_openai_backend_uses_chat_completions_shape` |
| `--backend openai` requires `--host` | `TestCLIArgGating.test_openai_backend_without_host_errors` |
| Model auto-resolution (Ollama default, alphabetical fallback) | `TestResolveModel` (all cases) |
| Explicit `--model` validated against what's available | `TestResolveModel.test_explicit_model_available_returned`, `test_explicit_model_missing_exits`, `test_openai_explicit_model_missing_exits` |
| Backend unreachable → clear per-backend error | `TestListModels.test_unreachable_exits_with_backend_specific_hint`, `TestCallLLM.test_connection_refused_gives_clear_error` |
| HTTP error surfaces server's response body | `TestCallLLM.test_http_error_surfaces_response_body_message` |
| Oversized input truncated before sending to the model | `TestCallLLM.test_truncates_oversized_content` |

## TODO

- [x] Support [LM Studio](https://lmstudio.ai) (OpenAI-compatible local
      server) as an alternative backend to Ollama.
- [x] Support other local runtimes generically (llama.cpp server, vLLM, any
      OpenAI-compatible `/v1/chat/completions` endpoint) via `--backend openai`
      + `--host`.

## License

MIT
