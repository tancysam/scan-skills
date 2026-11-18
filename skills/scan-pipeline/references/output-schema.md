# pipeline.json contract

`scripts/render_pipeline.py` enforces everything below. Unknown fields are
rejected. Audit-clean examples: `example-system.json`, `example-function.json`.

## Root

| field | rule |
| --- | --- |
| `version` | `1` |
| `format` | `"pipeline-scan"` |
| `project` | `{ name ≤48, slug (lowercase-dashed ≤48), tagline? ≤100, date YYYY-MM-DD }` |
| `mode` | `"system"` (abstracted stages) or `"function"` (function-level) |
| `scope` | optional `{ files?, loc?, pipelineFiles?, focus? ≤100 }` — from `measure_repo.py` |
| `stats` | `{ pipelines, stages, models, datasets }`, integers 0–9999 (true totals) |
| `frameworks` | optional, ≤ 12 × `{ id, label ≤40, role? ≤40, domain? }` |
| `pipelines` | 1–12 × pipeline (below) |
| `graph` | `{ nodes, edges }` |

## pipelines[]

| field | rule |
| --- | --- |
| `id` | unique, `[A-Za-z0-9_.:/-]`, ≤ 80 |
| `name` | ≤ 28, unique (case-insensitive) — the lane title |
| `type` | `training` · `fine-tuning` · `inference` · `batch-inference` · `rag` · `agent` · `etl` · `feature` · `evaluation` · `labeling` · `streaming` · `other` |
| `trigger` | ≤ 48, optional — what starts a run ("daily 01:00 UTC", "POST /ask") |
| `detail` | ≤ 240, optional |
| `sourceRef` | ≤ 160, optional, repo-relative |

## graph.nodes (1–80)

| field | rule |
| --- | --- |
| `id` | unique, `[A-Za-z0-9_.:/-]`, ≤ 80 — keep stable across scans |
| `label` | ≤ 32 — stage name (system) or `function()` (function view) |
| `kind` | `trigger` · `data` · `transform` · `retrieve` · `model` · `train` · `evaluate` · `agent` · `tool` · `store` · `output` · `monitor` · `function` |
| `sub` | ≤ 56 — what it does (system) or its signature (function view) |
| `detail` | ≤ 240 |
| `pipeline` | a `pipelines[].id`; omit for shared nodes |
| `sourceRef` | ≤ 160, repo-relative `path[:line]`; no leading `/`, no `..`, no URL |
| `framework` | ≤ 32 (PyTorch, XGBoost, LangChain, Spark…) |
| `domain` | bare domain for hosted models/services |
| `steps` | ≤ 12 × `{ name ≤56, sourceRef? ≤160, note? ≤80 }` — key functions / callees |
| `config` | ≤ 8 entries `{ "key ≤32": "value ≤64" }` — non-secret settings |
| `capabilities` | ≤ 6 unique strings ≤ 24 |
| `confidence` | `high` · `medium` · `low` (`low` renders dashed) |

## graph.edges (≤ 160)

| field | rule |
| --- | --- |
| `from`, `to` | existing node ids; no self-loops |
| `kind` | `feeds` · `calls` · `reads` · `writes` · `triggers` · `produces` · `loads` |
| `label` | ≤ 32 — the artifact that moves (`list[Chunk]`, `model.pt`) |

Exact duplicate edges are rejected.

## Renderer behavior

- Each pipeline is a lane (left → right); lanes stack in `pipelines` order.
  Shared nodes sit in the band after the first lane that touches them.
- `tool` nodes called by another node fold into the caller's card.
- Credential-like strings anywhere (including config keys/values) fail the render.
- `audit:` lines flag: pipelines with no nodes, one node, no internal edges,
  or no visible start; training without evaluation; `train` nodes that produce
  nothing; isolated nodes; under-wired maps; maps with no model/train/agent;
  function view without `sourceRef`; system view without `steps` or with > 50
  nodes; `stats` below what's drawn.
- Re-rendering keeps `pipeline.rendered.json` / `pipeline.previous.json` next
  to the input for the "Since last scan" card (`--previous`, `--no-diff`).
