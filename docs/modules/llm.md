# `llm` — the pluggable LLM layer

Stage 2 module. One interface over six providers, plus the "ask for JSON, validate it,
retry on failure" machine every generation call in this project needs (probe questions,
graded MC items, teach-back scoring, misconception hypotheses). Nothing here is
stateful, and nothing here writes to the learner event log — per CONTRACTS.md hard rule
5, only the learner core recomputes numbers; this module only talks to a model and
hands back typed, provenance-tagged results.

Source: `learning_tutor/llm/`. Reused code: IDEA.md *Reused code* — `ai_providers.py`
was reused from earlier work of mine on 2026-09-05 and is
**no longer verbatim**: its ten hardcoded `timeout=30.0` / `timeout=60.0` arguments now
read `LT_LLM_TIMEOUT_S`, and `ClaudeProvider` follows the current Messages API (see
*Changes to the copied file* below). Nothing else in it differs. The `generate_structured` shape follows the same earlier pattern.

## Files

| File | What it is |
|---|---|
| `ai_providers.py` | The copied file (715 lines + a header comment + the timeout helper). `AIProviderInterface`, six concrete providers (`GoogleProvider`, `OpenAIProvider`, `ClaudeProvider`, `OllamaProvider`, `LMStudioProvider`, `CustomProvider`), `AIProviderFactory`. |
| `config.py` | `ProviderConfig`, `get_provider_config(role)`, the tutor/solver split, `solver_differs_from_tutor()`, cache TTL/dir helpers. All env-driven, no machine-specific defaults except `http://localhost:11434` for Ollama (itself overridable). |
| `structured.py` | `extract_json`, `generate_text`, `generate_structured`, `GenerationMeta`, `SameSolverError`, the on-disk cache. |
| `router.py` | FastAPI `APIRouter` (`/llm/generate`, `/llm/structured`, `/llm/health`) for the gateway to mount. Imports cleanly with no network access. |

## Env vars

| Var | Default | Meaning |
|---|---|---|
| `LT_LLM_PROVIDER` | `ollama` | Default provider for both roles. One of `google`, `openai`, `claude`, `ollama`, `lm_studio`, `custom`. |
| `LT_LLM_MODEL` | `llama3.1:8b` | Default model for both roles. |
| `LT_LLM_BASE_URL` | unset | Default base URL override for both roles (e.g. for `custom`). |
| `OLLAMA_URL` | `http://localhost:11434` | Base URL used when the resolved provider is `ollama`. |
| `ANTHROPIC_API_KEY` | unset | Used when provider resolves to `claude`. |
| `OPENAI_API_KEY` | unset | Used when provider resolves to `openai`. |
| `GOOGLE_API_KEY` | unset | Used when provider resolves to `google`. |
| `LT_LLM_TUTOR_PROVIDER` / `LT_LLM_TUTOR_MODEL` / `LT_LLM_TUTOR_BASE_URL` / `LT_LLM_TUTOR_API_KEY` | unset (falls back to the unscoped default) | Tutor-role overrides. |
| `LT_LLM_SOLVER_PROVIDER` / `LT_LLM_SOLVER_MODEL` / `LT_LLM_SOLVER_BASE_URL` / `LT_LLM_SOLVER_API_KEY` | unset (falls back to the unscoped default) | Solver-role overrides. Leaving these unset means the solver resolves to the **same** provider+model as the tutor — see *Role split* below. |
| `LT_LLM_ALLOW_SAME_SOLVER` | off | Truthy (`1`/`true`/`yes`/`on`) disables the solver-differs-from-tutor guard. Escape hatch for local dev with a single Ollama model; never set in a real evidence-gathering run. |
| `LT_LLM_TEMPERATURE` | `0.2` | Generation temperature. |
| `LT_LLM_MAX_TOKENS` | `4000` | Generation max tokens. |
| `LT_LLM_TIMEOUT_S` | `120` | Seconds to wait on one provider HTTP call. Read per call, so it can change without a restart. |
| `LT_LLM_CACHE_TTL_S` | `604800` (7 days) | TTL for the on-disk structured/text cache. `0` disables caching entirely (every call hits the provider). |
| `LT_DATA_DIR` | `./data` | Read directly by this module (not via `learning_tutor.config`, to keep `llm` free of a hard dependency on the learner core) to locate the cache directory. |

## Role split: tutor vs. solver

CONTRACTS.md's evidence rules require that the same model never authors a question,
answers it as the "blind solver", *and* judges the result — otherwise a validated item
isn't independent evidence, it's the model agreeing with itself. This module enforces
the narrow slice of that rule it can see:

- `get_provider_config("tutor")` and `get_provider_config("solver")` resolve
  independently from `LT_LLM_TUTOR_*` / `LT_LLM_SOLVER_*` env vars (falling back to the
  unscoped `LT_LLM_PROVIDER` / `LT_LLM_MODEL` when unset).
- `solver_differs_from_tutor()` compares the resolved `(provider, model)` pairs.
- `generate_structured(..., role="solver")` (and `generate_text(..., role="solver")`)
  call this check **before** making any provider call, and raise `SameSolverError` if
  the pairs match and `LT_LLM_ALLOW_SAME_SOLVER` is not set. The router turns this into
  an HTTP 409.

The verified pair is tutor `llama3.1:8b`, solver `gemma3:12b`; `.env.example` and
`docker-compose.yml` set both. With no solver override at all, the solver resolves to the
tutor's own model and the guard above refuses every solver call unless
`LT_LLM_ALLOW_SAME_SOLVER` is set.

This is a runtime guard, not a full solution — it only catches "solver and tutor
resolved to literally the same provider+model," not more subtle non-independence (e.g.
two different quantizations of the same base model). The item-validation workflow in
`learner/items.py` still records `evaluation_method`, and only `blind_solver`, `rubric`,
and `human` can move an item past `TEACHING_ONLY` (CONTRACTS.md), so a solver misconfigured
to match the tutor is caught twice: once here at generation time, once implicitly by
which evaluation methods are trusted downstream.

## `generate_structured` / `generate_text`

```python
from pydantic import BaseModel
from learning_tutor.llm import generate_structured, generate_text

class GradedItem(BaseModel):
    stem: str
    options: list[str]
    answer: str

result, meta = generate_structured(
    "Write one MC question about covectors, with 3 options.",
    GradedItem,
    role="tutor",       # or pass an explicit ProviderConfig instead of role
    retries=2,          # up to 3 total attempts
    cache=True,
)
# result: GradedItem
# meta: GenerationMeta(provider=..., model=..., prompt_hash=..., latency_ms=..., cached=...)

text, meta = generate_text("Explain covectors in two sentences.", role="tutor")
```

Both are synchronous (they run the async provider call via `asyncio.run` internally) —
call them from sync code (CLI, a sync FastAPI route body) or, from async code, call the
provider from `ai_providers` directly with `await` instead. Calling them from inside a
running event loop raises `RuntimeError` rather than deadlocking.

### JSON extraction (`extract_json`)

Handles, in order: a fenced ` ```json ... ``` ` block; the response as-is; the outermost
balanced `{...}` or `[...]` span if the model wrapped the JSON in prose; and trailing
commas before a closing `}`/`]` in any of the above (tried with and without comma
stripping). Raises `ValueError` if nothing parses.

### Retry loop

On a parse or Pydantic validation failure, the error is appended to the *original*
prompt ("Your previous reply could not be parsed or failed validation (`<error>`).
Respond again with ONLY valid JSON...") and the model is asked again, up to
`retries + 1` total attempts. Exhausting retries raises `ValueError` with the last
error attached.

## Cache layout

```
LT_DATA_DIR/cache/llm/<sha256>.json
```

Key = `sha256(provider \0 model \0 prompt \0 schema_name)` — `schema_name` is
`"__text__"` for `generate_text`, or the Pydantic model's `__name__` for
`generate_structured`. Two different roles that resolve to the same provider+model
and are given the same prompt will therefore share a cache entry; that's intentional
(the cache key is about *what was asked and who would answer it*, not which role asked).

Entries are plain JSON: `{"text": "..."}` or `{"result": {...}}`. Staleness is checked
by file mtime vs. `LT_LLM_CACHE_TTL_S`; a `0` TTL skips both read and write, so every
call is live. A cache read that fails Pydantic validation against the *current* schema
(e.g. the schema changed since the entry was written) is treated as a miss rather than
an error — the module regenerates instead of surfacing a confusing validation error
from stale data.

The cache directory is resolved from `LT_DATA_DIR` directly (see env table above), not
through `learning_tutor.config.get_settings()`, so `learning_tutor.llm` has no import
dependency on the learner core.

## Pointing at host Ollama from Docker

`ai_providers.OllamaProvider` defaults to `http://localhost:11434`, which inside a
container is the container itself, not the host. Two ways to fix it (IDEA.md *Reused code*
names the compose layout this follows):

- Set `OLLAMA_URL=http://host.docker.internal:11434` in the gateway container's
  environment (works on Docker Desktop for Windows/Mac out of the box; on Linux add
  `extra_hosts: ["host.docker.internal:host-gateway"]` to the compose service).
- Or run Ollama itself in the compose network and use the service name
  (`http://ollama:11434`) — not the default here since Stage 0/1 assume an Ollama that is
  already running on the host.

## Router (`/llm/*`)

Mounted by the gateway (`gateway/app.py`, Stage 2) with `app.include_router(router)`.
Endpoints:

- `GET /llm/health` — returns configured tutor/solver provider+model, no network call.
- `POST /llm/generate` — `{"prompt", "role"?, "system_prompt"?, "cache"?}` → `{"text", "meta"}`.
- `POST /llm/structured` — `{"prompt", "schema", "role"?, "system_prompt"?, "retries"?, "cache"?}`
  → `{"result", "meta"}`. `schema` is a **flat** field-name → type-name map
  (`str`/`int`/`float`/`bool`, or their long forms) used to build a throwaway Pydantic
  model at request time — deliberately minimal, for callers with no Python schema
  module (e.g. `web-ui`). Anything richer (nested objects, lists, field validators)
  should import `learning_tutor.llm.structured.generate_structured` directly with a
  real Pydantic model, as `tutor/orchestrator.py` does.
- A `SameSolverError` from either endpoint surfaces as HTTP 409.

## Known limitations

- `AIProviderFactory.create_provider` construction is synchronous even though every
  provider method is `async`; the Google provider's `genai.configure()` call happens at
  construction time (no network I/O, just stores the key) so this is safe today but
  worth revisiting if a future provider does real setup work in `__init__`.
- `generate_text`/`generate_structured` cannot be called from inside a running asyncio
  event loop (they raise `RuntimeError` instead of deadlocking) — an async gateway route
  that needs this today must call the provider from `ai_providers` directly with
  `await`, or run the sync call in a thread pool. The router's handlers are defined as
  regular (sync) functions for this reason, which is fine under FastAPI/Starlette (sync
  routes run in a thread pool automatically).
- The solver-differs-from-tutor guard only compares `(provider, model)` strings. It
  cannot detect "different model name, same weights" or "same model, different
  quantization" — those still require a human policy decision, not code.
- The providers' `transcribe_audio` methods (Whisper for OpenAI, inline audio for Google) are
  unused by this project, which transcribes lecture audio through `LT_TRANSCRIBE_URL`
  instead; they stay only because the shared interface declares them.
- The router's dynamic-schema endpoint (`/llm/structured`) has no way to express
  `list[...]`, nested objects, or field constraints — it is a convenience surface, not
  a replacement for a real Pydantic model.
- No streaming support anywhere in this module; every call blocks until the full
  response is available. Fine for question/grading generation; would need rework for a
  chat-style UI.

## Changes to the copied file

### Timeouts

The original file hardcoded an httpx `timeout=` on every provider call: 30 s in four
places, 60 s in six. Nothing above it could raise them, and a local model on a plan-sized
prompt exceeds them: a 12B local model (`gemma3:12b`) hit the 60 s Ollama timeout every
time, surfacing as an `httpx.ReadTimeout` after a full minute of nothing. That is why the
default is now 120 s.

All ten now read one module-level helper:

```python
# learning_tutor/llm/ai_providers.py
def request_timeout() -> float:
    """Seconds to wait on one provider HTTP call: LT_LLM_TIMEOUT_S, default 120."""
```

which delegates to `config.request_timeout()`, with a local `os.environ` fallback so the
file still stands alone if it is ever lifted back out. It is read **per call**, not at
import, so a process can change the timeout without a restart — and so a test can
monkeypatch the environment. `tests/test_llm_ai_providers.py` asserts the configured value
reaches every provider's `generate_text` and `generate_chat` through a fake httpx client,
and that no literal timeout survives in the file.

Raising the ceiling does not make a model faster. It means a slow call finishes instead of
failing; the cost is a request that can hold a worker for two minutes.

### The Claude provider

`ClaudeProvider` calls `POST /v1/messages` over raw httpx. Current Claude models (use
`LT_LLM_MODEL=claude-opus-5-5`, or `LT_LLM_TUTOR_MODEL` / `LT_LLM_SOLVER_MODEL` per role)
reject non-default sampling parameters and always run adaptive thinking, whose tokens count
toward `max_tokens`. So the provider:

- sends no `temperature` (the argument is accepted and ignored, to keep the shared
  interface);
- sends `max_tokens` of at least 16000 in both `generate_text` and `generate_chat`;
- checks `stop_reason`: `refusal` raises `ClaudeRefusalError` (with the `stop_details`
  category), `max_tokens` raises `ClaudeTruncatedError`;
- returns the first content block whose `type` is `text`, because thinking blocks can come
  first.

This path is covered by mocked HTTP tests only (`tests/test_llm_ai_providers.py`); no live
API call has been made from this project. A non-streaming call with a large `max_tokens`
can run long, so raise `LT_LLM_TIMEOUT_S` if Claude calls time out.

The file stays in ruff's `extend-exclude`: reformatting the other 700 lines would bury these
diffs against the original.

## Tests

`tests/test_llm_ai_providers.py`, `tests/test_llm_config.py`, `tests/test_llm_structured.py`,
`tests/test_llm_router.py`. All provider calls are replaced with a `FakeProvider`
injected by monkeypatching `AIProviderFactory.create_provider` — no network access in
any test. Run with:

```bash
uv run pytest tests/test_llm_*.py -q
```

(`fastapi` and `httpx` are runtime dependencies in `pyproject.toml`; `pytest` is in the
`dev` dependency group, which `uv sync` installs by default.)
