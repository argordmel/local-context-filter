# Skill: Local Context Filter

*[Read in English](README.md)*

Una skill para [Claude Code](https://claude.com/claude-code) (`local-context-filter`) que
delega contexto grande/crudo a un LLM **local** antes de que llegue a Claude:

- **`--grep`** — búsqueda regex recursiva real (como `grep -rn`), con raíz en
  el directorio actual, sin escapar hacia arriba. Sin LLM, cero tokens de
  Claude — la opción más barata y precisa cuando sabes el string/patrón
  literal que buscás.
- **`--task`** (modo filtro LLM) — pasa un archivo/directorio/stdin por un
  modelo local con una descripción de tarea; el modelo elimina todo lo
  irrelevante y solo el resultado compacto entra al contexto de Claude.
- **`--grep` + `--task`** — grep primero (exacto, gratis), y luego el modelo
  local reduce una lista de coincidencias ruidosa a lo relevante.
- **`--diff`** — filtra `git diff HEAD` en el directorio actual (o acotado
  a `--input`); diff crudo (gratis) sin `--task`, o filtrado por el modelo
  con `--task`.
- **`--ls`** — lista recursivamente archivos/directorios bajo `--input`
  (por defecto: directorio actual), sin LLM, cero tokens de Claude; usala
  en vez de `bash ls`/`tree` para explorar la estructura del proyecto.
- **`--find`** — encuentra archivos/directorios por nombre (glob, como
  `find -iname`), sin LLM, cero tokens de Claude; usala en vez de
  `bash find` para localizar un archivo por nombre.
- **`--count`** — cuenta líneas por archivo bajo `--input` (como `wc -l`),
  sin LLM, cero tokens de Claude; usala en vez de `bash wc -l`.
- **`--run`** — corre un comando `npm`/`npx`/`pnpm`/`yarn` (solo esos
  cuatro binarios están permitidos) e imprime su salida; cruda (gratis)
  sin `--task`, o filtrada por el modelo con `--task`.
- **`--report` / `--clean`** — lee o borra el log local de uso
  (`usage.json`) que trackea tokens ahorrados estimados a lo largo del tiempo.

Soportado por [Ollama](https://ollama.com) (por defecto), [LM Studio](https://lmstudio.ai),
o cualquier otro servidor compatible con OpenAI (llama.cpp server, vLLM, ...)
vía `--backend openai --host <url>`. Seleccionable con `--backend`. Ver [TODO](#todo).

## Por qué

Leer un log grande, un directorio entero, o un doc largo en una conversación
de Claude gasta tokens de contexto en ruido. Esta skill hace ese filtrado
localmente y gratis — Claude solo ve el resultado ya destilado.

## Instalación

```bash
git clone git@github.com:argordmel/local-context-filter.git ~/.claude/skills/local-context-filter
```

Con eso alcanza — Claude Code auto-descubre cualquier skill bajo
`~/.claude/skills/`. No hay paso de registro adicional.

Si ya tienes un `~/.claude/skills/local-context-filter` (de un setup manual
anterior), eliminalo primero, o clonalo en otro lado y symlinkealo:

```bash
git clone git@github.com:argordmel/local-context-filter.git ~/code/local-context-filter
ln -s ~/code/local-context-filter ~/.claude/skills/local-context-filter
```

### Requisitos

- Python 3 (solo stdlib — sin dependencias que instalar)
- `--grep` solo no necesita ningún backend, es Python puro.
- Para el modo `--task` (filtro LLM), uno de:
  - **Ollama** (por defecto): `ollama serve` corriendo localmente, modelo
    pulled, ej. `ollama pull qwen2.5:7b`.
  - **LM Studio**: servidor local iniciado (pestaña Developer > Start
    Server, puerto 1234 por defecto) con un modelo cargado; pasar
    `--backend lmstudio`.
  - **Cualquier otro servidor compatible con OpenAI** (llama.cpp server,
    vLLM, ...): `--backend openai --host http://host:puerto` — `--host` es
    obligatorio, no hay puerto convencional que asumir.

**Ollama auto-detecta el modelo que ya tienes corriendo.** Sin `--model`,
consulta `/api/ps` para ver qué modelo está cargado en memoria en Ollama y
usa ese — sin tiempo extra de carga, siempre coincide con lo que ya tienes
levantado. Si no hay nada cargado, cae a `qwen2.5:7b` si está pulled, o si
no, al primero alfabéticamente entre los pulled.

**LM Studio auto-detecta el modelo que ya tienes corriendo.** Sin
`--model`, consulta el `/api/v0/models` propio de LM Studio (que expone un
campo `state: loaded/not-loaded` por modelo) y usa el cargado — el
`/v1/models` plano lista todo modelo *descargado* sin importar si está
cargado, así que no sirve para esto. Si no hay nada cargado o falla la
consulta, cae al primer resultado de `/v1/models`.

**Sin modelo default hardcodeado para el backend openai genérico.** Sin
`--model`, toma el primer modelo en el orden que reporta `/v1/models` —
no hay forma estándar de preguntarle a un servidor OpenAI-compatible
genérico cuál modelo está "cargado". Pasá `--model` explícito si quieres
uno específico.

Solo un backend necesita correr a la vez — elige uno con `--backend`.

**Nada arranca un servicio por ti — ni Ollama, ni LM Studio.** Prender o
apagar el backend es tu responsabilidad; el script solo verifica si es
alcanzable y falla con un error claro (y el comando para arreglarlo) si no
lo es. Claude tampoco avisa antes — corre el comando directo y solo se
entera por ese error si el backend está caído.

**Cómo decirle a Claude qué tienes corriendo:** simplemente dile en el
chat, en texto plano — ej. *"tengo Ollama levantado"* o *"tengo LM Studio
arriba"*. No hace falta sintaxis especial; Claude pasa el `--backend`
correcto en los comandos que corra el resto de esa conversación. Sin
mencionar un modelo, toma el que cargue primero (ver arriba) — menciona
uno específico solo si quieres que se use ese en particular. Dilo de
nuevo en una sesión futura si quieres que se recuerde ahí también — no se
persiste automáticamente.

## Uso

```bash
# búsqueda exacta, todo el proyecto actual, sin LLM, sin tokens de Claude
python3 ~/.claude/skills/local-context-filter/filter.py --grep "ServiceOrder"

# igual, sin distinguir mayúsculas, acotado a una subcarpeta
python3 ~/.claude/skills/local-context-filter/filter.py --grep "todo" --ignore-case --input app

# filtrar con LLM un archivo/directorio grande hasta lo que importa (Ollama, por defecto)
python3 ~/.claude/skills/local-context-filter/filter.py \
  --task "encontrar la causa raíz del timeout" --input server.log --max-words 200

# igual, vía LM Studio
python3 ~/.claude/skills/local-context-filter/filter.py \
  --backend lmstudio \
  --task "encontrar la causa raíz del timeout" --input server.log --max-words 200

# igual, vía cualquier otro servidor compatible con OpenAI
python3 ~/.claude/skills/local-context-filter/filter.py \
  --backend openai --host http://localhost:8080 \
  --task "encontrar la causa raíz del timeout" --input server.log --max-words 200

# grep primero, y que el modelo local reduzca una lista de coincidencias ruidosa
python3 ~/.claude/skills/local-context-filter/filter.py --grep "TODO" --task "cuáles TODOs son sobre auth"

# diff crudo del working tree, todo el repo, sin LLM, sin tokens de Claude
python3 ~/.claude/skills/local-context-filter/filter.py --diff

# igual, acotado a un archivo
python3 ~/.claude/skills/local-context-filter/filter.py --diff --input README.md

# igual, filtrado por tarea
python3 ~/.claude/skills/local-context-filter/filter.py --diff --task "qué cambios tocan auth"

# listado recursivo de directorio, sin LLM, sin tokens de Claude
python3 ~/.claude/skills/local-context-filter/filter.py --ls

# igual, acotado a una subcarpeta
python3 ~/.claude/skills/local-context-filter/filter.py --ls --input app/config

# encontrar un archivo por nombre (glob), sin LLM, sin tokens de Claude
python3 ~/.claude/skills/local-context-filter/filter.py --find "*.log"

# igual, sin distinguir mayúsculas
python3 ~/.claude/skills/local-context-filter/filter.py --find "readme*" --ignore-case

# contar líneas por archivo, sin LLM, sin tokens de Claude
python3 ~/.claude/skills/local-context-filter/filter.py --count --input filter.py
python3 ~/.claude/skills/local-context-filter/filter.py --count

# correr un comando npm/npx/pnpm/yarn, salida cruda, sin LLM
python3 ~/.claude/skills/local-context-filter/filter.py --run "npm install"

# igual, filtrado por tarea
python3 ~/.claude/skills/local-context-filter/filter.py --run "yarn install" --task "qué errores bloquean el install"

# resumen de uso / borrar el log local de uso
python3 ~/.claude/skills/local-context-filter/filter.py --report
python3 ~/.claude/skills/local-context-filter/filter.py --clean
```

Referencia completa de flags y comportamiento: [SKILL.md](SKILL.md).

## Alias de shell (recomendado)

Escribir la ruta completa `python3 ~/.claude/skills/local-context-filter/filter.py`
cada vez es tedioso para comandos sueltos rápidos como `--report`/`--clean`.
Agregá un alias a tu shell rc (`~/.zshrc`, `~/.bashrc`):

```bash
alias skill-local-context='python3 ~/.claude/skills/local-context-filter/filter.py'
```

Recargá el shell (`source ~/.zshrc`) y usalo directo:

```bash
skill-local-context --report
skill-local-context --clean
skill-local-context --grep "TODO" --input app
```

Esto es comodidad para vos corriendo comandos a mano — Claude igual
invoca `filter.py` por su ruta completa sin importar los alias de tu
shell, porque no carga tus archivos rc de shell interactivo.

## Activarla por defecto

Las skills en `~/.claude/skills/` son **globales** — disponibles en todo
proyecto, automáticamente, en cuanto están instaladas. Claude Code lee el
`description` de cada skill y decide por su cuenta cuándo es relevante; no
hay paso de "habilitar" separado ni nada específico por proyecto que
configurar.

Para que Claude realmente **prefiera** esta skill sobre sus herramientas
nativas de lectura/grep para búsquedas exactas (en vez de solo tenerla
disponible), agrega una línea así a tu `~/.claude/CLAUDE.md` global (aplica
a todo proyecto) o al `CLAUDE.md` de un proyecto:

```markdown
# Local search
For exact string/regex search in a project, prefer the `local-context-filter` skill's `--grep` mode over reading files directly or built-in grep tools — it costs zero Claude context tokens. Only fall back to reading files when full file content/formatting is needed, not just matching lines.

For reviewing/summarizing the current working tree changes, prefer the same skill's `--diff` mode (`git diff HEAD` under the hood) over running `git diff` and pasting its output into context — `--diff` alone (no `--task`) is free too. Add `--task` only when the raw diff is too noisy and needs filtering by a local model.

Never run `bash ls`, `find`, or `tree` to explore a project's file/directory structure — always use the same skill's `--ls` mode to list a folder, or `--find` to locate a file by name. Both are free, zero Claude context tokens, same as `--grep`. This applies even to a simple one-off "list the files in X" request — don't reach for Bash out of habit.

Never run `bash wc -l` to count lines in a file or directory — use the same skill's `--count` mode instead, same zero-token principle.

For npm/npx/pnpm/yarn commands (install, etc.), prefer the same skill's `--run` mode over running them directly with Bash — raw output is free, and `--task` filters noisy install logs down to real errors through the local model.
```

Sin esa instrucción, Claude igual *descubre* la skill vía su descripción
cuando una tarea parece encajar — la línea en CLAUDE.md solo hace la
preferencia explícita y consistente en vez de una decisión caso por caso.

## Log de uso (local, privado)

Cada corrida agrega una línea a `usage.json` junto a `filter.py` —
gitignored, nunca sale de tu máquina. Cada entrada es solo conteos:
`{ts, mode, backend, chars_in, chars_out, tokens_saved_est}` — sin
contenido de archivos, rutas, ni el texto de `--task`. `tokens_saved_est`
es una aproximación tosca (`chars/4`) al tokenizer real, útil como
tendencia, no como cifra exacta. Sobrescribí la ruta con
`LOCAL_CONTEXT_FILTER_LOG=/ruta/al/archivo`; si no se puede escribir, la
corrida igual funciona — el fallo de log es silencioso y nunca bloquea el
comando. `--report` imprime totales (por modo); `--clean` borra el log.

**Auto-mantenido, sin trabajo manual:** una vez que el log pasa las 6000
líneas se recorta automáticamente a las 5000 más recientes en la próxima
corrida — nunca crece sin límite, y no necesitas acordarte de correr
`--clean` vos mismo. `--clean` sigue ahí para cuando realmente quieras
resetear el contador a cero.

## Excludes por proyecto

`--grep`, `--ls`, `--find`, y `--count` saltan `.git`, `node_modules`, `dist`,
`build`, `.venv`, `__pycache__`, `.next`, `coverage` por defecto. Para
saltar más directorios en un proyecto sin tocar la skill, agregá
`.claude/local-context-filter.json` en la raíz del proyecto:

```json
{"exclude": ["fixtures", "vendor"]}
```

## Seguridad

- Confinamiento de rutas: `--input` (y la raíz de búsqueda de `--grep`)
  puede entrar libremente a subdirectorios pero nunca puede resolver por
  encima del directorio desde donde corriste el comando — `../`, rutas
  absolutas fuera de él, y symlinks que apunten afuera son todos
  rechazados.
- `--grep`, `--ls`, `--find`, y `--count` saltan automáticamente `.git`,
  `node_modules`, `dist`, `build`, `.venv`, `__pycache__`, `.next`,
  `coverage` (más los excludes de proyecto de arriba); `--grep` y
  `--find` además tienen un tope de 500 coincidencias.
- `--run` solo ejecuta `npm`/`npx`/`pnpm`/`yarn` — cualquier otro binario
  se rechaza antes de correr, y el comando se tokeniza (nunca pasa por una
  shell), o sea sin inyección de shell vía `--run`. Sigue siendo ejecución
  real de código (scripts postinstall, etc.), igual que correrlo vos
  mismo.
- El modo filtro LLM divide entradas de más de ~24k caracteres en chunks
  secuenciales y filtra cada uno por separado (warning en stderr con la
  cantidad de chunks) — nada se pierde, solo toma una llamada al modelo
  por chunk en vez de una sola total.

## Tests

```bash
python3 ~/.claude/skills/local-context-filter/test_filter.py -v
```

`unittest`, solo stdlib. Mockea todas las llamadas de red (Ollama/LM
Studio/genérico compatible con OpenAI) y usa repos git temporales reales
para `--diff` — no hace falta ningún servidor corriendo para que pasen.

### Checklist de cobertura

| Funcionalidad | Test(s) |
|---|---|
| `--grep` búsqueda exacta, números de línea | `TestGrepSearch.test_finds_matches_with_line_numbers`, `TestCLIEndToEnd.test_grep_prints_matches` |
| `--grep --ignore-case` | `TestGrepSearch.test_case_insensitive`, `TestCLIEndToEnd.test_grep_ignore_case_flag` |
| `--grep` sin coincidencias → `NO_MATCHES` | `TestGrepSearch.test_no_matches_returns_empty_list`, `TestCLIEndToEnd.test_grep_no_matches_prints_sentinel` |
| `--grep` salta `.git`/`node_modules`/etc. | `TestGrepSearch.test_skips_excluded_dirs` |
| `--grep` tope de 500 coincidencias + warning | `TestGrepSearch.test_hits_match_cap_and_warns` |
| `--grep` salta archivos binarios/no legibles | `TestGrepSearch.test_binary_file_skipped_without_crash` |
| `--diff` repo completo, crudo | `TestGitDiff.test_unstaged_change_shows_in_diff`, `TestGitDiff.test_staged_change_shows_in_diff`, `TestCLIEndToEnd.test_diff_prints_raw_diff` |
| `--diff --input` acotado a una ruta | `TestGitDiff.test_path_scopes_diff_to_single_file`, `TestCLIEndToEnd.test_diff_scoped_to_input` |
| `--diff` árbol limpio → `NO_CHANGES` | `TestGitDiff.test_no_changes_returns_empty_string`, `TestCLIEndToEnd.test_diff_no_changes_prints_sentinel` |
| `--diff` fuera de un repo git → error | `TestGitDiff.test_not_a_git_repo_errors`, `TestCLIEndToEnd.test_diff_not_a_git_repo_errors` |
| `--diff` + `--grep` mutuamente excluyentes | `TestCLIArgGating.test_diff_and_grep_mutually_exclusive` |
| `--ls` lista archivos/directorios, salta excluidos | `TestListTree.test_lists_files_and_dirs`, `TestListTree.test_skips_excluded_dirs`, `TestCLIEndToEnd.test_ls_prints_entries` |
| `--ls` directorio vacío → `NO_ENTRIES` | `TestListTree.test_empty_dir_returns_empty_list`, `TestCLIEndToEnd.test_ls_empty_dir_prints_sentinel` |
| `--ls` acotado a `--input` | `TestCLIEndToEnd.test_ls_scoped_to_input` |
| `--ls` con `--input` no-directorio → error | `TestListTree.test_non_directory_exits` |
| `--ls` mutuamente excluyente con `--grep`/`--diff` | `TestCLIEndToEnd.test_ls_and_grep_mutually_exclusive`, `test_ls_and_diff_mutually_exclusive` |
| `--find` matchea basenames por glob, `--ignore-case` | `TestFindSearch.test_finds_files_by_glob`, `test_ignore_case`, `TestCLIEndToEnd.test_find_prints_matches` |
| `--find` sin coincidencias → `NO_MATCHES` | `TestCLIEndToEnd.test_find_no_matches_prints_sentinel` |
| `--find` salta excluidos, excludes custom | `TestFindSearch.test_skips_excluded_dirs`, `test_custom_excluded_dirs` |
| `--find` mutuamente excluyente con `--grep` | `TestCLIArgGating.test_find_and_grep_mutually_exclusive` |
| Excludes de proyecto (`.claude/local-context-filter.json`) | `TestProjectExcludes` (todos los casos), `TestCLIEndToEnd.test_find_respects_project_excludes_config` |
| `--count` archivo único, directorio + `TOTAL`, sentinel dir vacío | `TestCLIEndToEnd.test_count_single_file`, `test_count_directory_includes_total`, `test_count_empty_dir_prints_sentinel` |
| `--count` mutuamente excluyente con `--diff` | `TestCLIEndToEnd.test_count_and_diff_mutually_exclusive` |
| `--run` rechaza binarios no permitidos | `TestCLIEndToEnd.test_run_rejects_non_allowed_binary` |
| `--run` ejecuta binario permitido, imprime salida | `TestCLIEndToEnd.test_run_npm_version_prints_output` |
| `--run` mutuamente excluyente con `--grep` | `TestCLIEndToEnd.test_run_and_grep_mutually_exclusive` |
| `--report` totales por modo, sentinel `NO_USAGE_DATA` | `TestGenerateReport` (todos los casos), `TestCLIEndToEnd.test_report_no_data_prints_sentinel`, `test_report_after_usage_shows_totals` |
| `--report`/`--clean` mutuamente excluyentes con otros modos | `TestCLIArgGating.test_report_and_grep_mutually_exclusive`, `test_clean_and_ls_mutually_exclusive`, `test_clean_and_report_mutually_exclusive` |
| `--clean` borra usage.json, idempotente | `TestCLIEndToEnd.test_clean_removes_log_and_is_idempotent` |
| Log de uso rota pasadas 6000 líneas, conserva las 5000 más recientes, intacto bajo el gatillo | `TestLogUsage.test_log_rotates_once_over_trigger_keeping_most_recent`, `test_log_stays_under_trigger_untouched`, `TestRotateUsageLog` (todos los casos) |
| Entrada sobredimensionada de `--task` en chunks (no truncada), resultados unidos | `TestCallLLMChunked` (todos los casos) |
| Confinamiento de rutas (`../`, absolutas, symlink hacia afuera) | `TestConfineToRoot` (todos los casos, incl. `test_symlink_pointing_outside_root_rejected`) |
| `--task` leyendo archivo / directorio / stdin | `TestReadInput`, `TestReadDirectory` |
| `--task` sin `--input` y sin stdin → error | `TestReadInput.test_no_input_and_no_stdin_exits` |
| Backend Ollama: listar modelos, llamada generate | `TestListModels.test_ollama_parses_names`, `TestCallLLM.test_ollama_returns_response_field` |
| Backend LM Studio: listar modelos, chat completions | `TestListModels.test_lmstudio_parses_ids`, `TestCallLLM.test_lmstudio_returns_message_content` |
| Backend genérico `openai` | `TestListModels.test_openai_parses_ids_same_shape_as_lmstudio`, `TestCallLLM.test_openai_backend_uses_chat_completions_shape` |
| `--backend openai` requiere `--host` | `TestCLIArgGating.test_openai_backend_without_host_errors` |
| Auto-resolución de modelo (modelo cargado en Ollama/LM Studio, default, fallbacks) | `TestResolveModel` (todos los casos) |
| Detección de modelo cargado en Ollama (`/api/ps`) | `TestRunningOllamaModel` (todos los casos) |
| Detección de modelo cargado en LM Studio (`/api/v0/models`) | `TestRunningLmstudioModel` (todos los casos) |
| `--model` explícito validado contra lo disponible | `TestResolveModel.test_explicit_model_available_returned`, `test_explicit_model_missing_exits`, `test_openai_explicit_model_missing_exits` |
| Backend inalcanzable → error claro por backend | `TestListModels.test_unreachable_exits_with_backend_specific_hint`, `TestCallLLM.test_connection_refused_gives_clear_error` |
| Error HTTP muestra el mensaje del cuerpo de la respuesta | `TestCallLLM.test_http_error_surfaces_response_body_message` |
| Entrada sobredimensionada truncada antes de enviarse al modelo | `TestCallLLM.test_truncates_oversized_content` |

## TODO

- [x] Soportar [LM Studio](https://lmstudio.ai) (servidor local compatible
      con OpenAI) como backend alternativo a Ollama.
- [x] Soportar otros runtimes locales genéricamente (llama.cpp server,
      vLLM, cualquier endpoint compatible con OpenAI
      `/v1/chat/completions`) vía `--backend openai` + `--host`.
- [x] Soportar listado de directorio (`--ls`, estilo `ls`) a costo cero de
      tokens de Claude.
- [x] Soportar búsqueda de archivos por nombre (`--find`, glob estilo
      `find -iname`) a costo cero de tokens de Claude.
- [x] Log local de uso (`usage.json`) con `--report`/`--clean`.
- [x] Dividir en chunks entradas sobredimensionadas de `--task` en vez de
      truncarlas.
- [x] Excludes extra por proyecto vía `.claude/local-context-filter.json`.

## Licencia

MIT
