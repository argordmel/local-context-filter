# Skill: Local Context Filter

*[Read in English](README.md)*

Una skill de [Claude Code](https://claude.com/claude-code) (`local-context-filter`) que
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
- **`--diff`** — filtra `git diff HEAD` en el directorio actual; diff crudo
  (gratis) sin `--task`, o filtrado por el modelo con `--task`.

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

**Sin modelo default hardcodeado para LM Studio/openai.** Solo Ollama
tiene uno (`qwen2.5:7b`). Sin `--model`, LM Studio y openai siempre toman
el modelo que ordena primero alfabéticamente entre lo que reporte
`/v1/models` — no es un modelo "preferido", es puro orden alfabético
sobre lo que tengas cargado en tu máquina. Pasá `--model` explícito si
quieres uno específico.

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

# diff crudo del working tree, sin LLM, sin tokens de Claude
python3 ~/.claude/skills/local-context-filter/filter.py --diff

# igual, filtrado por tarea
python3 ~/.claude/skills/local-context-filter/filter.py --diff --task "qué cambios tocan auth"
```

Referencia completa de flags y comportamiento: [SKILL.md](SKILL.md).

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
# Búsqueda local
Para búsqueda exacta de strings/regex en este repo, preferir el modo
`--grep` de la skill `local-context-filter` sobre leer archivos
directamente — cuesta cero tokens de contexto. Solo recurrir a leer
archivos cuando se necesite ver el contenido completo, no solo las líneas
que matchean.
```

Sin esa instrucción, Claude igual *descubre* la skill vía su descripción
cuando una tarea parece encajar — la línea en CLAUDE.md solo hace la
preferencia explícita y consistente en vez de una decisión caso por caso.

## Seguridad

- Confinamiento de rutas: `--input` (y la raíz de búsqueda de `--grep`)
  puede entrar libremente a subdirectorios pero nunca puede resolver por
  encima del directorio desde donde corriste el comando — `../`, rutas
  absolutas fuera de él, y symlinks que apunten afuera son todos
  rechazados.
- `--grep` salta automáticamente `.git`, `node_modules`, `dist`, `build`,
  `.venv`, `__pycache__`, `.next`, `coverage`, y tiene un tope de 500
  coincidencias.
- El modo filtro LLM trunca entradas de más de ~24k caracteres (con
  warning en stderr) para entrar en la ventana de contexto del modelo —
  divide escaneos grandes de directorios en corridas más chicas en vez de
  una sola pasada sobre todo.

## Tests

```bash
python3 ~/.claude/skills/local-context-filter/test_filter.py -v
```

`unittest`, solo stdlib. Mockea todas las llamadas de red (Ollama/LM
Studio/genérico compatible con OpenAI) y usa repos git temporales reales
para `--diff` — no hace falta ningún servidor corriendo para que pasen.

## TODO

- [x] Soportar [LM Studio](https://lmstudio.ai) (servidor local compatible
      con OpenAI) como backend alternativo a Ollama.
- [x] Soportar otros runtimes locales genéricamente (llama.cpp server,
      vLLM, cualquier endpoint compatible con OpenAI
      `/v1/chat/completions`) vía `--backend openai` + `--host`.

## Licencia

MIT
