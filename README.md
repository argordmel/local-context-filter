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
- **`--ls`** — recursively lists files/dirs under `--input` (default: cwd),
  no LLM, zero Claude tokens; use instead of `bash ls`/`tree` for exploring
  project structure.
- **`--find`** — finds files/dirs by basename glob (like `find -iname`), no
  LLM, zero Claude tokens; use instead of `bash find` for locating a file
  by name.
- **`--count`** — counts lines per file under `--input` (like `wc -l`), no
  LLM, zero Claude tokens; use instead of `bash wc -l`.
- **`--run`** — runs an `npm`/`npx`/`pnpm`/`yarn` command (only these four
  binaries are allowed) and prints its output; raw (free) without
  `--task`, or model-filtered down to real errors with it.
- **`--report` / `--clean`** — read or wipe the local usage log
  (`usage.json`) that tracks estimated tokens saved over time.

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

**Ollama auto-picks the model you're already running.** Without `--model`,
it checks `/api/ps` for whatever's currently loaded in Ollama's memory and
uses that — no extra load time, always matches your actual setup. Falls
back to `qwen2.5:7b` if pulled, else alphabetically first pulled model, if
nothing is currently loaded.

**LM Studio auto-picks the model you're already running.** Without
`--model`, it checks LM Studio's own `/api/v0/models` (which exposes a
`state: loaded/not-loaded` field per model) and uses the loaded one —
plain `/v1/models` lists every *downloaded* model regardless of load
state, so it can't answer this. Falls back to the first `/v1/models`
entry if nothing is loaded or the state check fails.

**No hardcoded default model for generic openai backend.** Without
`--model`, it picks the first model in the order `/v1/models` reports —
there's no standard way to ask a generic OpenAI-compatible server which
model is "loaded". Pass `--model` explicitly if you want a specific one.

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

# recursive directory listing, no LLM, no Claude tokens
python3 ~/.claude/skills/local-context-filter/filter.py --ls

# same, scoped to a subfolder
python3 ~/.claude/skills/local-context-filter/filter.py --ls --input app/config

# find a file by basename glob, no LLM, no Claude tokens
python3 ~/.claude/skills/local-context-filter/filter.py --find "*.log"

# same, case-insensitive
python3 ~/.claude/skills/local-context-filter/filter.py --find "readme*" --ignore-case

# count lines per file, no LLM, no Claude tokens
python3 ~/.claude/skills/local-context-filter/filter.py --count --input filter.py
python3 ~/.claude/skills/local-context-filter/filter.py --count

# run an npm/npx/pnpm/yarn command, raw output, no LLM
python3 ~/.claude/skills/local-context-filter/filter.py --run "npm install"

# same, filtered by task
python3 ~/.claude/skills/local-context-filter/filter.py --run "yarn install" --task "which errors are blocking install"

# usage summary / wipe the local usage log
python3 ~/.claude/skills/local-context-filter/filter.py --report
python3 ~/.claude/skills/local-context-filter/filter.py --clean
```

Full flag reference and behavior notes: [SKILL.md](SKILL.md).

## Shell alias (recommended)

Typing the full `python3 ~/.claude/skills/local-context-filter/filter.py`
path each time is tedious for quick one-offs like `--report`/`--clean`.
Add an alias to your shell rc (`~/.zshrc`, `~/.bashrc`):

```bash
alias skill-local-context='python3 ~/.claude/skills/local-context-filter/filter.py'
```

Reload the shell (`source ~/.zshrc`) and use it directly:

```bash
skill-local-context --report
skill-local-context --clean
skill-local-context --grep "TODO" --input app
```

This is a convenience for you running commands by hand — Claude still
invokes `filter.py` via its own full path regardless of aliases in your
shell config, since it doesn't source your interactive shell rc files.

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

Never run `bash ls`, `find`, or `tree` to explore a project's file/directory structure — always use the same skill's `--ls` mode to list a folder, or `--find` to locate a file by name. Both are free, zero Claude context tokens, same as `--grep`. This applies even to a simple one-off "list the files in X" request — don't reach for Bash out of habit.

Never run `bash wc -l` to count lines in a file or directory — use the same skill's `--count` mode instead, same zero-token principle.

For npm/npx/pnpm/yarn commands (install, etc.), prefer the same skill's `--run` mode over running them directly with Bash — raw output is free, and `--task` filters noisy install logs down to real errors through the local model.
```

Without that instruction, Claude will still *discover* the skill via its
description when a task looks like a fit — the CLAUDE.md line just makes the
preference explicit and consistent instead of judgment-call-by-judgment-call.

## Usage log (local, private)

Every run appends one line to `usage.json` next to `filter.py` — gitignored,
never leaves the machine. Each entry is just counts: `{ts, mode, backend,
chars_in, chars_out, tokens_saved_est}` — no file contents, paths, or
`--task` text. `tokens_saved_est` is a rough `chars/4` proxy for the real
tokenizer, useful as a trend, not an exact figure. Override the path with
`LOCAL_CONTEXT_FILTER_LOG=/path/to/file`; if it can't be written, the run
still succeeds — logging failures are silent and never block the command.
`--report` prints totals (by mode); `--clean` deletes the log.

**Self-maintaining, no manual upkeep needed:** once the log passes 6000
lines it's automatically trimmed to the most recent 5000 on the next run
— it never grows unbounded, and you don't need to remember to run
`--clean` yourself. `--clean` is still there for when you actually want
to reset the count to zero.

## Project-level excludes

`--grep`, `--ls`, `--find`, and `--count` skip `.git`, `node_modules`, `dist`,
`build`, `.venv`, `__pycache__`, `.next`, `coverage` by default. To skip
more directories in one project without editing the skill, add
`.claude/local-context-filter.json` at the project root:

```json
{"exclude": ["fixtures", "vendor"]}
```

## Safety

- Path confinement: `--input` (and `--grep`'s search root) can go into
  subdirectories freely but can never resolve above the directory you ran
  the command from — `../`, absolute paths outside it, and symlinks
  pointing out are all rejected.
- `--grep`, `--ls`, `--find`, and `--count` auto-skip `.git`, `node_modules`,
  `dist`, `build`, `.venv`, `__pycache__`, `.next`, `coverage` (plus any
  project excludes above); `--grep` and `--find` also cap at 500 matches.
- `--run` only executes `npm`/`npx`/`pnpm`/`yarn` — any other binary is
  rejected before it runs, and the command is tokenized (never passed to a
  shell), so no shell injection via `--run`. It's still real code
  execution (postinstall scripts, etc.), same as running it yourself.
- The LLM-filter mode splits input over ~24k chars into sequential chunks
  and filters each separately (a stderr warning names the chunk count) —
  nothing is dropped, it just takes one LLM call per chunk instead of one
  total.

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
| `--grep --input` scoped to a single file | `TestGrepSearch.test_single_file_input_is_searched`, `test_single_file_input_no_matches`, `TestCLIEndToEnd.test_grep_input_single_file_is_searched` |
| `--diff` whole repo, raw | `TestGitDiff.test_unstaged_change_shows_in_diff`, `TestGitDiff.test_staged_change_shows_in_diff`, `TestCLIEndToEnd.test_diff_prints_raw_diff` |
| `--diff --input` scoped to one path | `TestGitDiff.test_path_scopes_diff_to_single_file`, `TestCLIEndToEnd.test_diff_scoped_to_input` |
| `--diff` clean tree → `NO_CHANGES` | `TestGitDiff.test_no_changes_returns_empty_string`, `TestCLIEndToEnd.test_diff_no_changes_prints_sentinel` |
| `--diff` outside a git repo → error | `TestGitDiff.test_not_a_git_repo_errors`, `TestCLIEndToEnd.test_diff_not_a_git_repo_errors` |
| `--diff` + `--grep` mutually exclusive | `TestCLIArgGating.test_diff_and_grep_mutually_exclusive` |
| `--ls` lists files/dirs, skips excluded dirs | `TestListTree.test_lists_files_and_dirs`, `TestListTree.test_skips_excluded_dirs`, `TestCLIEndToEnd.test_ls_prints_entries` |
| `--ls` empty dir → `NO_ENTRIES` | `TestListTree.test_empty_dir_returns_empty_list`, `TestCLIEndToEnd.test_ls_empty_dir_prints_sentinel` |
| `--ls` scoped to `--input` | `TestCLIEndToEnd.test_ls_scoped_to_input` |
| `--ls` non-directory `--input` → error | `TestListTree.test_non_directory_exits` |
| `--ls` mutually exclusive with `--grep`/`--diff` | `TestCLIEndToEnd.test_ls_and_grep_mutually_exclusive`, `test_ls_and_diff_mutually_exclusive` |
| `--find` matches basenames by glob, `--ignore-case` | `TestFindSearch.test_finds_files_by_glob`, `test_ignore_case`, `TestCLIEndToEnd.test_find_prints_matches` |
| `--find` no matches → `NO_MATCHES` | `TestCLIEndToEnd.test_find_no_matches_prints_sentinel` |
| `--find` skips excluded dirs, custom excludes | `TestFindSearch.test_skips_excluded_dirs`, `test_custom_excluded_dirs` |
| `--find` mutually exclusive with `--grep` | `TestCLIArgGating.test_find_and_grep_mutually_exclusive` |
| Project excludes (`.claude/local-context-filter.json`) | `TestProjectExcludes` (all cases), `TestCLIEndToEnd.test_find_respects_project_excludes_config` |
| `--count` single file, directory + `TOTAL`, empty dir sentinel | `TestCLIEndToEnd.test_count_single_file`, `test_count_directory_includes_total`, `test_count_empty_dir_prints_sentinel` |
| `--count` mutually exclusive with `--diff` | `TestCLIEndToEnd.test_count_and_diff_mutually_exclusive` |
| `--run` rejects non-allowed binaries | `TestCLIEndToEnd.test_run_rejects_non_allowed_binary` |
| `--run` executes allowed binary, prints output | `TestCLIEndToEnd.test_run_npm_version_prints_output` |
| `--run` mutually exclusive with `--grep` | `TestCLIEndToEnd.test_run_and_grep_mutually_exclusive` |
| `--report` totals by mode, `NO_USAGE_DATA` sentinel | `TestGenerateReport` (all cases), `TestCLIEndToEnd.test_report_no_data_prints_sentinel`, `test_report_after_usage_shows_totals` |
| `--report`/`--clean` mutually exclusive with other modes | `TestCLIArgGating.test_report_and_grep_mutually_exclusive`, `test_clean_and_ls_mutually_exclusive`, `test_clean_and_report_mutually_exclusive` |
| `--clean` deletes usage.json, idempotent | `TestCLIEndToEnd.test_clean_removes_log_and_is_idempotent` |
| Usage log rotates past 6000 lines, keeps most recent 5000, untouched below trigger | `TestLogUsage.test_log_rotates_once_over_trigger_keeping_most_recent`, `test_log_stays_under_trigger_untouched`, `TestRotateUsageLog` (all cases) |
| Oversized `--task` input chunked (not truncated), results joined | `TestCallLLMChunked` (all cases) |
| Path confinement (`../`, absolute, symlink escape) | `TestConfineToRoot` (all cases, incl. `test_symlink_pointing_outside_root_rejected`) |
| `--task` reading a file / directory / stdin | `TestReadInput`, `TestReadDirectory` |
| `--task` with no `--input` and no stdin → error | `TestReadInput.test_no_input_and_no_stdin_exits` |
| Ollama backend: list models, generate call | `TestListModels.test_ollama_parses_names`, `TestCallLLM.test_ollama_returns_response_field` |
| LM Studio backend: list models, chat completions | `TestListModels.test_lmstudio_parses_ids`, `TestCallLLM.test_lmstudio_returns_message_content` |
| Generic `openai` backend | `TestListModels.test_openai_parses_ids_same_shape_as_lmstudio`, `TestCallLLM.test_openai_backend_uses_chat_completions_shape` |
| `--backend openai` requires `--host` | `TestCLIArgGating.test_openai_backend_without_host_errors` |
| Model auto-resolution (currently-loaded ollama/lmstudio model, default, fallbacks) | `TestResolveModel` (all cases) |
| Currently-loaded Ollama model detection (`/api/ps`) | `TestRunningOllamaModel` (all cases) |
| Currently-loaded LM Studio model detection (`/api/v0/models`) | `TestRunningLmstudioModel` (all cases) |
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
- [x] Support directory listing (`--ls`, `ls`-style) at zero Claude
      token cost.
- [x] Support filename search (`--find`, `find -iname`-style glob) at zero
      Claude token cost.
- [x] Local-only usage log (`usage.json`) with `--report`/`--clean`.
- [x] Chunk oversized `--task` input instead of truncating it.
- [x] Per-project extra excludes via `.claude/local-context-filter.json`.

## License

MIT
