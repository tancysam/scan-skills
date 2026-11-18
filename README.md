<p align="center"><img src="assets/logo.png" width="160" alt="scan-skills pixel-art binoculars logo"></p>

# scan-skills

Two agent skills that turn a repository into a private, interactive HTML map:
one for architecture and AI usage, the other for AI/ML pipelines. Use them to
follow a request, understand a data flow, or see what changed since your last
scan. Each map is a single self-contained page that works fully offline.

Available as a Claude Code plugin or as plain skills for Claude Code and Codex.

> [!IMPORTANT]
> These skills were inspired by and heavily based on Foglamp's codebase mapping
> implementation. I wanted a local, fully offline version, which is why I
> created this set of skills.

## Quick start

Requires **Python 3.9+**. The bundled Python scripts use only the standard library;
there are no Python packages to install.

Inside Claude Code, install the plugin:

```text
/plugin marketplace add tancysam/scan-skills
/plugin install scan-skills@scan-skills
```

Then, in the repository you want to map, run either command:

```text
/scan-codebase
```

```text
/scan-pipeline
```

The agent reads the repository, writes a JSON description, and runs the bundled
renderer to validate, audit, and render it. Open the resulting local HTML file:

| Command | Description file | Interactive map |
| --- | --- | --- |
| `/scan-codebase` | `.scan-codebase/scan.json` | `.scan-codebase/index.html` |
| `/scan-pipeline` | `.scan-pipeline/pipeline.json` | `.scan-pipeline/index.html` |

These paths are relative to the **scanned repository**. The plugin commands are
also available as `/scan-skills:scan-codebase` and `/scan-skills:scan-pipeline`.

> If you previously linked these skills into `~/.claude/skills/`, remove those
> symlinks after installing the plugin to avoid duplicate skills.

## What you get

The map connects components or pipeline stages with labeled flows and includes
repository-relative file references so you can find the supporting code.
[elkjs](https://github.com/kieler/elkjs) handles layout and is inlined in the page.
No server is needed to view it.

- **Architecture maps** show entry points, agents, models, tools, workflows,
  services, stores, and third-party APIs. Models and tools appear inside their
  callers' cards; workflows group steps that run together.
- **Pipeline maps** show data and control flowing through lanes, read from left
  to right. Select a stage to inspect available signatures, file references,
  frameworks, non-secret settings, and key functions.
- **Repeat scans** compare against a saved snapshot. When something changes,
  the **Since last scan** card shows added, removed, and changed nodes, plus
  edge and stat changes. Stable node IDs make these comparisons meaningful.

| Action | Control |
| --- | --- |
| Trace a flow and inspect details | Click a node, then choose Downstream, Upstream, or Both |
| Move around the map | Drag to pan; ⌘/Ctrl + scroll or pinch to zoom |
| Search the map | `/` |
| Fit the map to the screen | `F` |
| Switch theme | `T` |
| Clear the current selection | `Esc` |
| Switch landscape/portrait layout (pipeline lanes/columns) | Rotate button |
| Spotlight related items or move to them | Hover or click items in the side panel |

## Choosing between the two skills

| Use | When you want to know | Detail level |
| --- | --- | --- |
| [`scan-codebase`](skills/scan-codebase/SKILL.md) | What is this system, how does it use AI, and what does it talk to? | Architecture and relationships across the repository |
| [`scan-pipeline`](skills/scan-pipeline/SKILL.md) | What happens to the data, step by step, and where is that code? | Functions for small pipelines; stages with drill-down steps for larger systems |

`scan-pipeline` covers LLM/RAG/agent flows, training, fine-tuning, ETL,
inference, and evaluation. It first runs `scripts/measure_repo.py` to estimate
the size of the pipeline code and recommend a view. You can also ask the agent
for a function-level view, a high-level view, or a specific pipeline.

## Privacy

The bundled renderer and generated page work locally and do not upload scans.

- **Offline page:** a strict CSP includes `default-src 'none'`,
  `connect-src 'none'`, and `img-src data:`. The page also disables `fetch`, XHR,
  WebSocket, EventSource, WebRTC, and `sendBeacon`. Domain names are metadata;
  logos are local monograms, never remote images.
- **Metadata only:** scans contain names, signatures, file references,
  descriptions, and non-secret settings—not source code, prompts, training
  samples, model output, environment values, or secrets. The renderer refuses
  credential-like strings.
- **Read-only investigation:** the skills treat repository content as untrusted
  data. They instruct the agent to read files without running project code,
  notebooks, training scripts, or installers.
- **Local output:** add `.scan-codebase/` and `.scan-pipeline/` to the scanned
  repository's `.gitignore`. The skills do this unless instructed otherwise.

These protections cover the skills' scan workflow, renderer, and output page.
The agent you use to read the repository has its own data-handling settings;
using these skills does not make a hosted agent run locally.

## Other installation methods and plugin management

### One-line plugin install

On **Claude Code 2.1.275+**, one command adds the marketplace and installs the
plugin:

```text
/plugin install scan-skills --marketplace tancysam/scan-skills
```

### Install from a terminal

```bash
claude plugin marketplace add tancysam/scan-skills
claude plugin install scan-skills@scan-skills
```

### Install from a local clone

Clone the repository if you don't have it yet:

```bash
git clone https://github.com/tancysam/scan-skills ~/scan-skills
```

Then register that local directory and install the plugin:

```bash
claude plugin marketplace add ~/scan-skills
claude plugin install scan-skills@scan-skills
```

### Update or uninstall

Inside Claude Code:

```text
/plugin marketplace update scan-skills
```

```text
/plugin uninstall scan-skills@scan-skills
```

### Install as plain skills

Clone the repository as shown above, then choose your agent's skill directory.
These symlinks are an alternative to installing the plugin.

For Claude Code:

```bash
mkdir -p ~/.claude/skills
ln -s ~/scan-skills/skills/scan-codebase ~/.claude/skills/scan-codebase
ln -s ~/scan-skills/skills/scan-pipeline ~/.claude/skills/scan-pipeline
```

For Codex:

```bash
mkdir -p ~/.codex/skills
ln -s ~/scan-skills/skills/scan-codebase ~/.codex/skills/scan-codebase
ln -s ~/scan-skills/skills/scan-pipeline ~/.codex/skills/scan-pipeline
```

For a single Claude Code project, create the links in that project's
`.claude/skills/` directory instead of `~/.claude/skills/`.

## Tests and local demo

Run these commands from the `scan-skills` repository root. Both test suites
are local-only:

```bash
python3 skills/scan-codebase/scripts/test_render_scan.py
python3 skills/scan-pipeline/scripts/test_render_pipeline.py
```

To try a pipeline map without an agent, render the bundled example:

```bash
mkdir -p /tmp/scan-skills-demo
cp skills/scan-pipeline/references/example-system.json /tmp/scan-skills-demo/pipeline.json
python3 skills/scan-pipeline/scripts/render_pipeline.py --input /tmp/scan-skills-demo/pipeline.json
```

Open `/tmp/scan-skills-demo/index.html` in a browser. On macOS:

```bash
open /tmp/scan-skills-demo/index.html
```

Both renderers accept `--input` and write `index.html` beside the input by
default. Use `--validate-only` to validate and audit without rendering, or
`--strict` to make audit warnings fail the command. See the
[codebase schema](skills/scan-codebase/references/output-schema.md) and
[pipeline schema](skills/scan-pipeline/references/output-schema.md) for the JSON
contracts.

## Repository layout

```text
.claude-plugin/plugin.json       Plugin manifest; loads ./skills/
.claude-plugin/marketplace.json  Single-plugin marketplace
skills/scan-codebase/            Architecture and AI usage skill
skills/scan-pipeline/            AI/ML pipeline skill
  SKILL.md                      Agent instructions
  scripts/                      Renderer and local tests; pipeline measurement
  references/                   Output schema and example JSON
  assets/                       HTML template and bundled elkjs
assets/                         Logo and its generation script
NOTICE                          Attribution and third-party license details
LICENSE                         Apache-2.0 license
```

Both skill directories contain their own instructions, scripts, references,
and assets; `measure_repo.py` belongs to `scan-pipeline`.

## License

[Apache-2.0](LICENSE). Derived from
[foglamp's open-source Scan](https://github.com/foglamp-labs/foglamp); see
[NOTICE](NOTICE) for attribution. [elkjs](https://github.com/kieler/elkjs) is
bundled under EPL-2.0; its license text is included in the bundled archive.
