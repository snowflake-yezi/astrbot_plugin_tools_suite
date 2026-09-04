# PROJECT KNOWLEDGE BASE

## OVERVIEW

This repository is an AstrBot plugin for per-conversation gold-price queries, group nickname mentions, and QQ/NapCat merged-forward expansion and deduplication. It targets Python 3.10+ and depends on AstrBot plus the packages in `requirements.txt`.

The package has no standalone CLI or server. AstrBot loads `ToolSuitePlugin` through `__init__.py`; platform behavior must be exercised in an AstrBot installation.

## STRUCTURE

| Path | Ownership |
| --- | --- |
| `__init__.py` | Package export for `ToolSuitePlugin`. |
| `main.py` | AstrBot event filters, per-scope settings, JSON persistence, nickname commands, and high-level forward orchestration. |
| `forward_records.py` | Recursive forward parsing, canonical fingerprints, flattened nodes, media normalization, and input limits. |
| `forward_dedup.py` | History migration/pruning, duplicate classification, node filtering, and the global history budget. |
| `onebot_forward.py` | OneBot/NapCat fetch, resend batching, action-result handling, and recall fallback. |
| `gold.py` | Gold/FX data sources, caching, formatting, and trend-chart rendering. |
| `tests/` | Standard-library unit tests for forward parsing, deduplication, limits, and OneBot payloads. |
| `metadata.yaml` | AstrBot plugin identity and release version. |
| `README.md` | User-facing commands, behavior, limits, and deployment notes. |
| `news.jpg` | Exact-duplicate reply image used by `main.py`. |
| `assets/` | Bundled chart font and its SIL OFL license. |

Runtime state is created at `data/tool_suite.json`; `data/` is not maintained source.

## WHERE TO LOOK

| Task | Location | Notes |
| --- | --- | --- |
| Add or change a chat command | `main.py` | Handlers are AstrBot `filter`-decorated methods on `ToolSuitePlugin`. |
| Change feature switches or stored settings | `main.py` | Scope keys are `group:<id>` or `private:<sender_id>` and default to disabled. |
| Change forward parsing or fingerprints | `forward_records.py` | Preserve leaf order and duplicate occurrences; container grouping is intentionally ignored. |
| Change duplicate decisions or history limits | `forward_dedup.py` | Partial matches use whole-leaf hashes; the byte budget spans all scopes. |
| Change OneBot/NapCat actions or payloads | `onebot_forward.py` | Keep `message_id`/`id` fetch compatibility and API/direct recall fallback. |
| Change enhanced-mode action policy | `main.py` | The handler chooses ignore, resend, or recall from the core decision. |
| Change gold providers or chart output | `gold.py` | Providers are attempted in order; chart rendering uses the non-interactive `Agg` backend. |
| Change public behavior or release version | `README.md`, `metadata.yaml` | Keep documented commands, limits, and version metadata aligned with code. |

## CONVENTIONS

- Keep network and OneBot calls asynchronous. `GoldPriceService` owns provider fallback and its in-memory caches.
- Persist settings through `_load_data()` and `_save_data()`; saving uses a temporary file followed by atomic replacement.
- Serialize forward classification and persistence with `_forward_lock` so concurrent events cannot lose records.
- Treat `FINGERPRINT_VERSION` in `forward_dedup.py` as a stored-data contract. If canonical fingerprint semantics change, update migration behavior and the README together.
- An incomplete forward expansion must not enter the deduplication store. Current limits are 16 forward layers, 256 structural levels, 5000 leaf messages, and 10000 components per record.
- Keep the global forward-history budget near 16 MiB and evict oldest records across scopes without removing feature settings or nickname data.
- Fingerprints may use stable media digests, IDs, or normalized locations, but must not download media. Resend normalization is separate from fingerprint normalization.
- Enhanced forward handling is intentionally silent to users; operational failures are logged. Basic mode retains reply messages and `news.jpg` behavior.
- Keep `news.jpg`, `assets/NotoSansSC-GoldChart.ttf`, and `assets/NotoSansSC-OFL.txt` packaged at their current relative paths.

## ANTI-PATTERNS

- Do not treat `data/tool_suite.json` as a checked-in fixture or hand-maintained configuration.
- Do not store partial expansion results or silently relax recursion/leaf limits.
- Do not include forwarding timestamps, container IDs, or node grouping in content identity; doing so breaks regrouped-record deduplication.
- Do not assume generic message-component shapes. The parser deliberately supports AstrBot objects, OneBot dictionaries, inline NapCat content, and fetched forward IDs.
- Do not replace or redistribute the bundled font without reviewing and preserving its license obligations.

## COMMANDS

Syntax-check all maintained Python modules without importing AstrBot or writing bytecode:

```bash
python -c "import ast, pathlib; files=['__init__.py','main.py','gold.py','forward_records.py','forward_dedup.py','onebot_forward.py']; [ast.parse(pathlib.Path(f).read_text(encoding='utf-8'), filename=f) for f in files]; print('AST parse OK')"
python -m unittest discover -s tests -v
```

Install runtime dependencies from `requirements.txt` inside the project-local environment required by the parent instructions. There is currently no lint, type-check, build, or CI configuration.

## NOTES

- Forward resend and recall require QQ/NapCat OneBot actions; group recall also depends on bot permissions and QQ role hierarchy.
- Gold queries depend on external services, so syntax checks cannot validate provider availability or response compatibility.
- Runtime integration checks should cover both group and private scope isolation, basic versus enhanced forward mode, nested inline/fetched forwards, and the packaged image/font paths.
