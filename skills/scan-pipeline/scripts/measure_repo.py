#!/usr/bin/env python3
"""Read-only repository sizing for scan-pipeline.

Walks the repo (never follows symlinks, never executes anything), counts source
files and lines, finds files with AI/ML pipeline signals, estimates how many
functions live in them, and recommends a view:

  function — pipeline code is small enough to map function by function
  system   — abstract subsystems/stages and list key functions as steps

Usage: python3 measure_repo.py [repo-root] [--json] [--focus SUBDIR]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, List

SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "env", ".env", "__pycache__", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", ".tox", "dist", "build", "out", ".next", ".nuxt", ".turbo", ".cache",
    "target", "vendor", "site-packages", ".ipynb_checkpoints", "coverage", ".gradle", ".idea", ".vscode",
    "wandb", "mlruns", "lightning_logs", "checkpoints", ".dvc", ".terraform", ".scan-pipeline", ".scan-codebase",
}
SOURCE_EXT = {
    ".py", ".ipynb", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".go", ".rs", ".java", ".kt", ".scala",
    ".rb", ".r", ".jl", ".cpp", ".cc", ".c", ".h", ".hpp", ".cu", ".swift", ".cs", ".php", ".sql",
    ".yaml", ".yml", ".toml",
}
CONFIG_EXT = {".yaml", ".yml", ".toml"}
MAX_FILE_BYTES = 1_000_000

SIGNALS: Dict[str, List[str]] = {
    "llm": [r"\bopenai\b", r"anthropic", r"@ai-sdk/", r"\bgenerateText\b", r"\bstreamText\b", r"\bgenerateObject\b",
            r"\bstreamObject\b", r"google\.genai|@google/genai|generativeai", r"\bbedrock", r"\bmistralai\b",
            r"\bcohere\b", r"\bgroq\b", r"\bollama\b", r"\blitellm\b", r"openrouter", r"chat\.completions",
            r"messages\.create", r"\bvllm\b"],
    "agent": [r"\blangchain\b|@langchain/", r"\blanggraph\b", r"llama_index|llamaindex", r"\bhaystack\b", r"\bdspy\b",
              r"\bcrewai\b", r"\bautogen\b", r"@mastra/", r"claude_agent_sdk|claude-agent-sdk", r"openai-agents|agents\.Agent",
              r"semantic_kernel", r"\bMcpServer\b|modelcontextprotocol", r"\btool\(\{"],
    "retrieval": [r"embeddings\.create|\bembedMany\b|\bembed\(\{|embed_documents|embed_query|\bget_embedding",
                  r"\bfaiss\b", r"\bchromadb\b|from chroma|@chroma", r"\bpinecone\b", r"\bweaviate\b", r"\bqdrant\b",
                  r"\bpgvector\b|<=>\s*\$|\bvector\(\d+\)", r"\bmilvus\b", r"\blancedb\b", r"sentence_transformers",
                  r"similarity_search|as_retriever|\bvectorstore\b|vector_store", r"\brerank(?:er|ing)?\b"],
    "training": [r"\bimport torch\b|\bfrom torch\b|\btorch\.", r"\btensorflow\b|\bkeras\b", r"\bjax\b|\bflax\b",
                 r"\bsklearn\b|scikit-learn", r"\bxgboost\b", r"\blightgbm\b", r"\bcatboost\b", r"\btransformers\b",
                 r"\bTrainer\b", r"\bpeft\b|\blora\b", r"\btrl\b", r"\baccelerate\b", r"\bdeepspeed\b",
                 r"pytorch_lightning|lightning\.", r"\.fit\(", r"optimizer\.step\(", r"loss\.backward\("],
    "data": [r"\bdatasets\b", r"\bpandas\b|\bpd\.read_", r"\bpolars\b", r"\bpyspark\b|\bspark\.", r"\bdask\b",
             r"\bpyarrow\b|\.parquet", r"\bduckdb\b", r"\bfeast\b", r"\bdvc\b", r"great_expectations", r"tokeniz"],
    "orchestration": [r"\bairflow\b", r"\bprefect\b", r"\bdagster\b", r"\bkubeflow\b|\bkfp\b", r"\bzenml\b",
                      r"\bmetaflow\b", r"\bkedro\b", r"\bray\b", r"\bcelery\b", r"\bbullmq\b", r"\binngest\b",
                      r"trigger\.dev", r"\bschedule\b|\bcron\b", r"\bluigi\b", r"sagemaker", r"vertex"],
    "serving": [r"\bfastapi\b", r"\bflask\b", r"\btriton\b", r"\btorchserve\b", r"\bbentoml\b", r"\bonnx", r"\bmlflow\b",
                r"\bseldon\b", r"\bkserve\b", r"\bgradio\b", r"\bstreamlit\b", r"\bmodal\b"],
    "evaluation": [r"\beval(?:uate|uation|s)?\b", r"\bmetrics?\b", r"\baccuracy\b|\bf1_score\b|\broc_auc", r"\bragas\b",
                   r"\bdeepeval\b", r"\bpromptfoo\b", r"\bbraintrust\b", r"\blangsmith\b", r"\bwandb\b",
                   r"\btensorboard\b", r"\bevidently\b"],
}
COMPILED = {k: [re.compile(p, re.I) for p in v] for k, v in SIGNALS.items()}
FUNC_RE = re.compile(
    r"^\s*(?:async\s+)?def\s+\w+|^\s*(?:export\s+)?(?:async\s+)?function\s+\w+|"
    r"^\s*(?:export\s+)?const\s+\w+\s*=\s*(?:async\s*)?(?:\([^)]*\)|\w+)\s*=>|^\s*func\s+|^\s*fn\s+\w+|"
    r"^\s*(?:public|private|protected)?\s*(?:static\s+)?[\w<>\[\]]+\s+\w+\s*\([^;]*\)\s*\{|"
    r"^\s*(?:static\s+)?(?:async\s+)?(?!(?:if|for|while|switch|catch|return|function)\b)[A-Za-z_$][\w$]*\s*\([^)]*\)\s*\{", re.M)
TEST_RE = re.compile(r"(^|/)(tests?|__tests__|spec|fixtures|mocks?)(/|$)|(\.|_)(test|spec)\.\w+$", re.I)


def read_text(path: Path) -> str:
    raw = path.read_bytes()
    if b"\x00" in raw[:4096]:
        return ""
    text = raw.decode("utf-8", errors="ignore")
    if path.suffix == ".ipynb":
        try:
            nb = json.loads(text)
            return "\n".join("".join(c.get("source", [])) for c in nb.get("cells", []) if c.get("cell_type") == "code")
        except (ValueError, AttributeError):
            return ""
    return text


def measure(root: Path) -> Dict[str, object]:
    files = loc = 0
    relevant: List[Dict[str, object]] = []
    categories: Dict[str, int] = {k: 0 for k in SIGNALS}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            path = Path(dirpath) / name
            if path.suffix.lower() not in SOURCE_EXT or path.is_symlink():
                continue
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    continue
                text = read_text(path)
            except OSError:
                continue
            if not text:
                continue
            rel = path.relative_to(root).as_posix()
            lines = text.count("\n") + 1
            is_config = path.suffix.lower() in CONFIG_EXT
            if not is_config:
                files += 1
                loc += lines
            if TEST_RE.search(rel):
                continue
            hits = {cat: sum(1 for rx in rxs if rx.search(text)) for cat, rxs in COMPILED.items()}
            # A file is pipeline code when it has a real AI/ML signal, not just generic data/eval words.
            core = hits["llm"] + hits["agent"] + hits["retrieval"] + hits["training"]
            support = hits["orchestration"] + hits["serving"] + hits["data"] + hits["evaluation"]
            if core == 0 and not (is_config and support >= 2):
                continue
            for cat, n in hits.items():
                if n:
                    categories[cat] += 1
            relevant.append({
                "path": rel, "lines": lines, "functions": 0 if is_config else len(FUNC_RE.findall(text)),
                "score": core * 3 + support, "signals": sorted(c for c, n in hits.items() if n),
            })
    relevant.sort(key=lambda r: (-int(r["score"]), str(r["path"])))
    rel_files = len(relevant)
    rel_loc = sum(int(r["lines"]) for r in relevant)
    rel_funcs = sum(int(r["functions"]) for r in relevant)
    mode = "function" if rel_files <= 25 and rel_funcs <= 80 and rel_loc <= 6000 else "system"
    reason = (f"{rel_files} pipeline files, ~{rel_funcs} functions, {rel_loc} lines — "
              + ("small enough to map each function and its calls" if mode == "function"
                 else "too large for one function-level map; abstract into stages and list key functions as steps"))
    return {
        "root": str(root), "sourceFiles": files, "sourceLines": loc,
        "pipelineFiles": rel_files, "pipelineLines": rel_loc, "pipelineFunctions": rel_funcs,
        "signalFiles": categories, "recommendedMode": mode, "reason": reason,
        "topFiles": relevant[:40],
    }


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description="Size a repository's AI/ML pipeline code (read-only).")
    parser.add_argument("root", nargs="?", default=".")
    parser.add_argument("--focus", help="Only measure this subdirectory")
    parser.add_argument("--json", action="store_true", help="Print JSON")
    args = parser.parse_args(argv)
    root = Path(args.root).expanduser().resolve()
    if args.focus:
        root = (root / args.focus).resolve()
    if not root.is_dir():
        print(f"error: not a directory: {root}", file=sys.stderr)
        return 1
    result = measure(root)
    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    print(f"Repo: {result['sourceFiles']} source files, {result['sourceLines']} lines")
    print(f"Pipeline code: {result['pipelineFiles']} files, {result['pipelineLines']} lines, ~{result['pipelineFunctions']} functions")
    print("Signals (files): " + ", ".join(f"{k} {v}" for k, v in result["signalFiles"].items() if v))
    print(f"Recommended view: {result['recommendedMode']} — {result['reason']}")
    print("Top pipeline files:")
    for r in result["topFiles"][:25]:
        print(f"  {r['path']}  ({r['lines']} lines, {r['functions']} fns; {', '.join(r['signals'])})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
