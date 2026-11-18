# scan.json contract (local Scan v2)

`scripts/render_scan.py` enforces everything below. Unknown fields are
rejected. A complete, audit-clean example lives in `example-scan.json`.

## Versions

- `version: 1` — exactly foglamp's public Scan contract. v1-only files render
  unchanged.
- `version: 2` — v1 plus the local extensions marked **v2** below.

## Root

| field | rule |
| --- | --- |
| `version` | `1` or `2` |
| `project` | object, see below |
| `stats` | `{ agents, models, tools, integrations }`, integers 0–9999 (true totals) |
| `topModels` | ≤ 3 rail items, ranked |
| `topTools` | ≤ 10 rail items |
| `topIntegrations` | ≤ 10 rail items |
| `workflows` **v2** | ≤ 12 unique strings ≤ 24; each must equal (case-insensitive) a node `group` |
| `graph` | `{ nodes, edges }` |

Rail item: `{ "id", "label", "domain?" }` — `id` 1–80 chars of
`[A-Za-z0-9_.:/-]`, unique per list; `label` ≤ 56.

## project

- `name` 1–48 · `slug` lowercase letters/digits/dashes, ≤ 48 · `date` `YYYY-MM-DD`
- `tagline` ≤ 80 (optional) · `iconDomain` ≤ 120 (optional, metadata only — never fetched)

## graph.nodes (1–60)

| field | rule |
| --- | --- |
| `id` | unique, 1–80 chars, `[A-Za-z0-9_.:/-]` — keep stable across scans |
| `label` | ≤ 28 |
| `kind` | `entry` · `cron` · `agent` · `model` · `tool` · `service` · `store` · `external` |
| `sub` | ≤ 40, optional — route, schedule, function, or short role |
| `detail` | ≤ 200, optional — one sentence shown on click |
| `group` | ≤ 24, optional — feature group, or a workflow listed in `workflows` |
| `domain` | optional bare domain (`openai.com`) — no scheme, no path |
| `sourceRef` | ≤ 120, optional repo-relative `path[:line]`; no leading `/`, no `..`, no URL |
| `capabilities` **v2** | ≤ 6 unique strings ≤ 24 — what the node can do (`tool loop`, `RAG`, …) |
| `confidence` **v2** | `high` · `medium` · `low` — evidence strength; `low` renders dashed |

## graph.edges (≤ 120)

| field | rule |
| --- | --- |
| `from`, `to` | existing node ids; no self-loops |
| `kind` | optional `calls` · `reads` · `writes` · `triggers` (shown while tracing) |
| `label` | ≤ 24, optional, always visible — business logic phrases |

Exact duplicate edges (same from, to, kind and label) are rejected.

## Renderer behavior worth knowing

- `agent|service|… → model|tool` edges fold the model/tool into the caller's
  card. Uncalled models/tools stay standalone and trigger an audit note.
- Groups lay out top-to-bottom; groups named in `workflows` lay out
  left-to-right with a workflow badge.
- Credential-like strings (API keys, tokens, private keys, JWTs, URLs with
  embedded passwords) make the render fail.
- `audit:` lines are quality warnings (fatal with `--strict`).
- Re-rendering keeps `scan.rendered.json` and `scan.previous.json` next to the
  input to power the "Since last scan" card (`--previous`, `--no-diff`).

Technology names and domains are free-form data. Do not omit a technology
because it is new to this skill or renderer.
