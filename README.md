# scan-skills

Two agent skills that map a repository into a private, interactive HTML page.
Everything runs on your machine. Nothing is uploaded.

| Skill | Answers | Invoke |
| --- | --- | --- |
| [`scan-codebase`](skills/scan-codebase/SKILL.md) | What is this system and what does it talk to? Entry points, agents, models, tools, workflows, services, stores, third-party APIs. | `/scan-codebase` |
| [`scan-pipeline`](skills/scan-pipeline/SKILL.md) | What happens to the data, step by step? LLM/RAG/agent flows, training, fine-tuning, ETL, inference, evaluation. Small repos are mapped function by function; large ones are abstracted into stages with drill-down steps. | `/scan-pipeline` |

The agent reads the code and writes a JSON description. A bundled Python
script validates and audits it, then renders a single self-contained
`index.html` (layout by [elkjs](https://github.com/kieler/elkjs), inlined).
Re-running a scan shows a "Since last scan" diff.

## Privacy

- The rendered page has a strict CSP (`default-src 'none'`, `connect-src 'none'`,
  `img-src data:`) and disables `fetch`, XHR, WebSocket, EventSource,
  WebRTC and `sendBeacon` at runtime.
- Scans hold metadata only (names, signatures, `file:line` refs, non-secret
  config). The renderer refuses to render credential-like strings.
- The skills treat repository content as untrusted data: they read files and
  never run project code, notebooks or installers.
- Output goes to `.scan-codebase/` or `.scan-pipeline/` in the scanned repo.
  Add those folders to the repo's `.gitignore`.

## Install

Requires Python 3.9+ (standard library only).

```bash
git clone <this repo> ~/scan-skills
mkdir -p ~/.claude/skills
ln -s ~/scan-skills/skills/scan-codebase ~/.claude/skills/scan-codebase
ln -s ~/scan-skills/skills/scan-pipeline ~/.claude/skills/scan-pipeline
```

For Codex, link the same folders into `~/.codex/skills/`. For a single
project, put them under that project's `.claude/skills/` instead.

## Test

```bash
python3 skills/scan-codebase/scripts/test_render_scan.py
python3 skills/scan-pipeline/scripts/test_render_pipeline.py
```

## Try it without an agent

```bash
mkdir -p /tmp/demo
cp skills/scan-pipeline/references/example-system.json /tmp/demo/pipeline.json
python3 skills/scan-pipeline/scripts/render_pipeline.py --input /tmp/demo/pipeline.json
open /tmp/demo/index.html
```

## License

Apache-2.0. Derived from foglamp's open-source Scan; see [NOTICE](NOTICE).
elkjs is bundled under EPL-2.0.
