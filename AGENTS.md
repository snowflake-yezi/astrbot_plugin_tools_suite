# PROJECT KNOWLEDGE BASE

## OVERVIEW

This repository is one AstrBot plugin with three independently owned capabilities: gold prices, group nickname mentions, and QQ/NapCat merged-forward processing. It targets Python 3.12 and AstrBot `>=4.9.2,<5`; `metadata.yaml` declares the `aiocqhttp` platform.

The package has no standalone service. AstrBot imports `ToolSuitePlugin` through `__init__.py`. Keep all decorated entry methods physically on that `Star` subclass in `main.py`: AstrBot discovers metadata globally, then binds handlers by the decorated function's exact module path.

## STRUCTURE

| Path | Ownership |
| --- | --- |
| `main.py` | AstrBot composition root, 21 decorated adapters, and cross-feature suite commands. |
| `core/config.py` | Stable plugin names and package/legacy paths. |
| `core/models.py` | Typed persisted-state and gold-market shapes. |
| `core/event.py` | Canonical group, scope, text, and mention extraction from AstrBot events. |
| `core/state.py` | Atomic JSON persistence and one-time legacy-state migration to AstrBot plugin data. |
| `features/gold/sources.py` | Ordered HTTP providers, payload conversion, FX cache, and trend retrieval. |
| `features/gold/service.py` | Quote cache, presentation formatting, and quote/trend use cases. |
| `features/gold/chart.py` | Non-interactive Matplotlib renderer and AstrBot temporary output path. |
| `features/gold/handler.py` | Gold enable/query event decisions and AstrBot results. |
| `features/nickname/domain.py` | Nickname parsing, longest-match lookup, collections, and binding decisions. |
| `features/nickname/handler.py` | Nickname state orchestration and official `At`/`Plain` result chains. |
| `features/forward/parser.py` | Recursive AstrBot/OneBot parsing, canonical fingerprints, media normalization, and limits. |
| `features/forward/dedup.py` | History migration/pruning, duplicate decisions, leaf filtering, and the global budget. |
| `features/forward/gateway.py` | NapCat/OneBot fetch, resend batching, action checks, and recall fallback. |
| `features/forward/handler.py` | Forward switches, retention, locking, reply policy, resend, and recall orchestration. |
| `tests/` | Pure unit tests plus real AstrBot component, registration, rendering, and OneBot contract checks. |

Maintained assets are `news.jpg`, `assets/NotoSansSC-GoldChart.ttf`, and `assets/NotoSansSC-OFL.txt`.

## WHERE TO LOOK

| Task | Primary location | Invariant |
| --- | --- | --- |
| Add or rename a chat entry | `main.py` | The decorated async-generator method stays on `ToolSuitePlugin` and delegates to a feature handler. |
| Change state shape or storage | `core/models.py`, `core/state.py` | Preserve `data/plugin_data/tool_suite/tool_suite.json` and successful-write-before-legacy-delete migration. |
| Change scope/event interpretation | `core/event.py` | Group keys are `group:<id>`; private keys are `private:<sender_id>`. |
| Change gold source behavior | `features/gold/sources.py` | Keep provider fallback ordered and network I/O asynchronous. |
| Change gold output | `features/gold/service.py`, `features/gold/chart.py` | Preserve the 90-second cache and `Agg` renderer. |
| Change nickname rules | `features/nickname/domain.py` | One user may have many nicknames, one nickname many users, and longest names match first. |
| Change forward identity | `features/forward/parser.py` | Preserve leaf order and duplicate occurrences; do not make grouping or timestamps part of identity. |
| Change duplicate history | `features/forward/dedup.py` | Partial matches use whole-leaf hashes; the 16 MiB budget spans every scope. |
| Change NapCat actions | `features/forward/gateway.py` | Retain `message_id` then `id` fetch compatibility and direct then legacy-API recall fallback. |
| Change enhanced policy | `features/forward/handler.py` | Enhanced mode stays silent; incomplete expansion never enters history. |
| Change public behavior/version | `README.md`, `metadata.yaml` | Keep commands, limits, platform, and AstrBot requirements aligned. |

## CONVENTIONS

- Reuse the canonical owners above; do not recreate event parsing, persistence, provider fallback, or OneBot calls in `main.py`.
- Keep feature-domain decisions independent from AstrBot where practical. Build `At`, `Plain`, `Reply`, and `Image` only in handlers/adapters.
- Use concise Simplified Chinese comments only for non-obvious protocol, persistence, or concurrency constraints.
- Persist state through `PluginStateStore`; writes use a temporary sibling followed by atomic replacement.
- Treat `FINGERPRINT_VERSION` as a stored-data contract. Update migration behavior and README whenever fingerprint semantics change.
- Keep forward limits at 16 real forward layers, 256 structural levels, 5000 leaves, and 10000 components unless requirements deliberately change.
- Do not download media for fingerprinting. Resend-location normalization and fingerprint normalization are separate concerns.
- Use official AstrBot result/message components first. Direct `call_action` belongs only at the NapCat/OneBot capability boundary.
- Do not add compatibility modules at removed root paths; internal modules are feature-owned and cleanly relocated.

## COMMANDS

Create the required project-local environment and install development dependencies:

```bash
uv venv .venv --python 3.12
uv pip install --python .venv -r requirements-dev.txt
```

Run the complete Windows verification suite:

```bash
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/ruff.exe check .
.venv/Scripts/ruff.exe format --check .
```

On macOS/Linux use `.venv/bin/` instead. A dependency-light fallback is:

```bash
python -m unittest discover -s tests -v
python -c "import ast, pathlib; files=[p for p in pathlib.Path('.').rglob('*.py') if '.venv' not in p.parts]; [ast.parse(p.read_text(encoding='utf-8'), filename=str(p)) for p in files]; print(f'AST parse OK: {len(files)} files')"
```

## NOTES

- Tests isolate AstrBot runtime writes under `.venv/astrbot-test-runtime`; do not let imports create repository `data/` fixtures.
- Automated tests use AstrBot's real registry/components and fake protocol actions. They do not prove a live QQ account's permissions, cache-file availability, or third-party gold endpoint availability.
- Group recall requires sufficient QQ role permissions. NapCat may still reject a structurally valid resend when its local media cache has expired.
- Runtime state lives under the hosting AstrBot instance's `data/plugin_data/tool_suite/`; generated charts live under `data/temp/tool_suite/`.
