# PROJECT KNOWLEDGE BASE

## OVERVIEW

One AstrBot plugin owns three independent capabilities: gold prices, group nickname mentions, and QQ/NapCat merged-forward processing. It targets Python 3.12 and AstrBot `>=4.9.2,<5`; `metadata.yaml` declares the `aiocqhttp` platform.

There is no standalone service. AstrBot imports `ToolSuitePlugin` through `__init__.py`. Decorated async-generator entry methods must stay physically on that `Star` subclass in `main.py`: AstrBot binds handlers by the decorated function's exact module path.

## WHERE TO LOOK

| Task | Location | Ownership / invariant |
| --- | --- | --- |
| Chat entries and suite commands | `main.py` | Composition root; delegate feature behavior to handlers. |
| Plugin names and resource paths | `core/config.py`, `assets/` | `PACKAGE_ROOT` anchors bundled resources independently of the working directory. |
| State shape and persistence | `core/models.py`, `core/state.py` | `PluginStateStore` owns atomic writes and successful-write-before-legacy-delete migration. |
| Event interpretation | `core/event.py` | Canonical text, mention and message-ID extraction; scopes are `group:<id>` or `private:<sender_id>`. |
| Gold providers | `features/gold/sources.py` | Ordered asynchronous fallback, FX cache, trend normalization and fresh/stale caches. |
| Gold output | `features/gold/service.py`, `features/gold/chart.py` | 90-second quote cache, formatting, 15-day trend use case, Matplotlib `Agg` rendering. |
| Gold event decisions | `features/gold/handler.py` | Enable/query policy and AstrBot results. |
| Nickname rules | `features/nickname/domain.py` | Many names per user, many users per name, longest-name matching first. |
| Nickname orchestration | `features/nickname/handler.py` | Scope state, binding/query flow, official `At` and `Plain` chains. |
| Forward parsing and identity | `features/forward/parser.py` | Recursive expansion, original-content flattened nodes, separate fingerprints and limits. |
| Forward history | `features/forward/dedup.py` | Migration, pruning, classification, most recent historical match and global history budget. |
| NapCat actions | `features/forward/gateway.py` | Fetch, ordered resend batches, action checks and recall fallbacks. |
| Forward event policy | `features/forward/handler.py` | Enable/enhanced switches, retention, locking, flattening, historical replies, mentions, recall and image-to-text fallback. |
| Regression and framework contracts | `tests/` | Domain tests, real AstrBot registration/components, rendering and fake OneBot actions. |
| Public behavior and requirements | `README.md`, `metadata.yaml` | Keep commands, limits, platform and AstrBot requirements aligned. |

## CONVENTIONS

- Keep domain decisions independent from AstrBot where practical. Build message components in handlers/adapters; direct `call_action` belongs at the OneBot gateway boundary.
- Reuse the owners above rather than rebuilding event parsing, persistence, providers or protocol actions in `main.py`. Do not add compatibility modules at removed root paths.
- Write concise Simplified Chinese comments only for non-obvious protocol, persistence or concurrency constraints. Ruff settings in `pyproject.toml` define formatting and lint rules.
- Persist through `PluginStateStore`; writes use a temporary sibling followed by atomic replacement. Feature handlers use the store's save operation; forward saves also enforce the history budget.
- Whole-record forward identity preserves flattened component order and duplicate occurrences, ignoring node grouping, sender and timestamps. Current-version partial matching uses whole-leaf hashes, not isolated shared components.
- Treat `FINGERPRINT_VERSION` as a persisted contract. Version 3 retains version-2 matching compatibility; unsupported versions clear only forward history. Update migration behavior and README when fingerprint semantics change.
- Preserve independent expansion limits: 16 real forward layers, 256 structural levels, 5000 leaves and 10000 components. Incomplete expansion never enters history; the 16 MiB history budget spans all scopes.
- Do not download media for fingerprinting. Fingerprint normalization must never alter resend content. Failed/incomplete recognition stays silent and does not resend.
- Enhanced mode controls only nested-forward flattening for new and partial matches: retain original content, order, available sender/time metadata and duplicate occurrences. Send at most 100 outer nodes per batch; do not re-nest, summarize or filter duplicates. New content gets no notice text; duplicate reminders and exact recall remain independent of enhanced mode.
- Partial matches quote the most recently stored matching original and mention the current sender. Exact matches attempt recall and still remind on recall failure; image construction/send failures fall back to text.
- History preserves the original message ID, insertion time and leaf fingerprints together. Do not overwrite them from a regrouped duplicate that may be recalled; `tests/test_forward_dedup.py` covers this invariant. Missing legacy IDs mean omitting the reply component, never quoting the current duplicate instead.
- Keep `get_forward_msg` parameter fallback from `message_id` to `id`, and recall fallback from `bot.call_action` to the legacy API object.

## COMMANDS

Run from the repository root. Create the project-local environment if absent, then install the full development dependencies:

```bash
uv venv .venv --python 3.12
uv pip install --python .venv -r requirements-dev.txt
```

Windows validation:

```bash
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/ruff.exe check .
.venv/Scripts/ruff.exe format --check .
```

On macOS/Linux use `.venv/bin/` instead. Async tests use `unittest.IsolatedAsyncioTestCase`; no `pytest-asyncio` plugin is required. Full test discovery requires the development dependencies: some test modules import runtime dependencies before any skip guard.

## NOTES

- Bundled resources are `assets/duplicate_forward.jpg`, `assets/NotoSansSC-GoldChart.ttf` and `assets/NotoSansSC-OFL.txt`. Keep the font license with the font.
- Runtime state belongs to the hosting AstrBot instance under `data/plugin_data/tool_suite/`; charts belong under `data/temp/tool_suite/`. The plugin-local `data/tool_suite.json` is only a migration source.
- Import `tests/astrbot_test_env.py` before AstrBot in tests that load the framework. It defaults `ASTRBOT_ROOT` to `.venv/astrbot-test-runtime`; integration subprocesses use temporary roots. Do not let test imports generate repository-root runtime data.
- Automated tests do not contact live QQ or gold providers. They cannot prove QQ recall permissions, historical reply availability, resend media-cache validity or current third-party endpoint behavior; those require target-instance checks.
