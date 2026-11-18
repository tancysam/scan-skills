<p align="center"><img src="assets/logo.png" width="160" alt="scan-skills pixel-art binoculars logo"></p>

# scan-skills

Two agent skills that map a repository into a private, interactive HTML page.
One is for architecture, the other is for AI/ML pipelines.

>[!important]
> This skill was inspired by and heavily built on based on Foglamp's codebase mapping implementation. 
> However, I wanted a local and fully offline skill to implement, which was why I created these sets of skills.
<br>

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

### As a Claude Code plugin (recommended)

Inside Claude Code:

```text
/plugin marketplace add tancysam/scan-skills
/plugin install scan-skills@scan-skills
```

On Claude Code 2.1.275 or later, one line does both:

```text
/plugin install scan-skills --marketplace tancysam/scan-skills
```

Or from a terminal:

```bash
claude plugin marketplace add tancysam/scan-skills
claude plugin install scan-skills@scan-skills
```

Then run `/scan-codebase` or `/scan-pipeline` (also available as
`/scan-skills:scan-codebase` and `/scan-skills:scan-pipeline`).

Update with `/plugin marketplace update scan-skills`. Remove with
`/plugin uninstall scan-skills@scan-skills`.

While this repo is private, installing needs GitHub access to it on that
machine (for example `gh auth login` followed by `gh auth setup-git`).

From a local clone (no GitHub needed):

```bash
git clone https://github.com/tancysam/scan-skills ~/scan-skills
claude plugin marketplace add ~/scan-skills
claude plugin install scan-skills@scan-skills
```

> If you previously linked the skills into `~/.claude/skills/`, remove those
> links after installing the plugin so each skill only shows up once.

### As plain skills (Claude Code, Codex)

```bash
git clone https://github.com/tancysam/scan-skills ~/scan-skills
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

## Repo layout

```text
.claude-plugin/plugin.json        plugin manifest (skills in ./skills/)
.claude-plugin/marketplace.json   single-plugin marketplace
skills/scan-codebase/             architecture + AI usage map
skills/scan-pipeline/             AI/ML pipeline map
assets/                           logo (make_logo.py regenerates it)
```

## License

Apache-2.0. Derived from foglamp's open-source Scan; see [NOTICE](NOTICE).
elkjs is bundled under EPL-2.0.
