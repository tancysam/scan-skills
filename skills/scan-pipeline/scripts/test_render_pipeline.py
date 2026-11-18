#!/usr/bin/env python3
"""Tests for the scan-pipeline contract, audit, diff, renderer and repo sizing."""

from __future__ import annotations

import copy
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
RENDERER = SCRIPT_DIR / "render_pipeline.py"
MEASURE = SCRIPT_DIR / "measure_repo.py"
TEMPLATE = SKILL_DIR / "assets" / "pipeline-template.html"
SYSTEM = SKILL_DIR / "references" / "example-system.json"
FUNCTION = SKILL_DIR / "references" / "example-function.json"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


rp = load("render_pipeline", RENDERER)
mr = load("measure_repo", MEASURE)


def example(path: Path = FUNCTION) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(RENDERER), *args], capture_output=True, text=True, timeout=30)


class ContractTests(unittest.TestCase):
    def test_examples_are_valid_and_audit_clean(self) -> None:
        for path in (SYSTEM, FUNCTION):
            data = rp.validate(example(path))
            self.assertEqual(rp.audit(data), [], path.name)
            self.assertEqual(rp.find_secrets(data), [], path.name)

    def test_requires_format_and_mode(self) -> None:
        data = example()
        data["format"] = "scan"
        with self.assertRaisesRegex(rp.ContractError, "format"):
            rp.validate(data)
        data = example()
        data["mode"] = "file"
        with self.assertRaisesRegex(rp.ContractError, "mode"):
            rp.validate(data)

    def test_node_pipeline_must_exist(self) -> None:
        data = example()
        data["graph"]["nodes"][0]["pipeline"] = "nope"
        with self.assertRaisesRegex(rp.ContractError, "is not a pipeline id"):
            rp.validate(data)

    def test_rejects_unknown_kinds_and_edge_kinds(self) -> None:
        data = example()
        data["graph"]["nodes"][0]["kind"] = "service"
        with self.assertRaisesRegex(rp.ContractError, "kind must be one of"):
            rp.validate(data)
        data = example()
        data["graph"]["edges"][0]["kind"] = "pipes"
        with self.assertRaisesRegex(rp.ContractError, "kind must be one of"):
            rp.validate(data)

    def test_steps_and_config_limits(self) -> None:
        data = example()
        data["graph"]["nodes"][3]["steps"] = [{"name": f"f{i}()"} for i in range(13)]
        with self.assertRaisesRegex(rp.ContractError, "at most 12 steps"):
            rp.validate(data)
        data = example()
        data["graph"]["nodes"][3]["config"] = {f"k{i}": "v" for i in range(9)}
        with self.assertRaisesRegex(rp.ContractError, "at most 8 entries"):
            rp.validate(data)
        data = example()
        data["graph"]["nodes"][3]["config"] = {"size": 800}
        with self.assertRaisesRegex(rp.ContractError, "must be a string"):
            rp.validate(data)

    def test_rejects_absolute_and_url_refs(self) -> None:
        for ref in ("/home/me/app.py", "../secrets.py", "https://x.dev/a.py", "C:\\code\\a.py"):
            data = example()
            data["graph"]["nodes"][2]["sourceRef"] = ref
            with self.assertRaisesRegex(rp.ContractError, "repository-relative", msg=ref):
                rp.validate(data)

    def test_rejects_dangling_and_self_edges(self) -> None:
        data = example()
        data["graph"]["edges"][0]["to"] = "ghost"
        with self.assertRaisesRegex(rp.ContractError, "missing node"):
            rp.validate(data)
        data = example()
        data["graph"]["edges"][0]["to"] = data["graph"]["edges"][0]["from"]
        with self.assertRaisesRegex(rp.ContractError, "itself"):
            rp.validate(data)


class AuditTests(unittest.TestCase):
    def test_training_without_eval_and_artifact(self) -> None:
        data = example(SYSTEM)
        data["graph"]["nodes"] = [n for n in data["graph"]["nodes"] if n["id"] != "evaluate"]
        data["graph"]["edges"] = [e for e in data["graph"]["edges"]
                                  if "evaluate" not in (e["from"], e["to"]) and e["from"] != "trainer"]
        warnings = "\n".join(rp.audit(rp.validate(data)))
        self.assertIn("shows no evaluation", warnings)
        self.assertIn("produces nothing", warnings)

    def test_function_view_needs_source_refs(self) -> None:
        data = example()
        del data["graph"]["nodes"][2]["sourceRef"]
        self.assertTrue(any("lack sourceRef" in w for w in rp.audit(rp.validate(data))))

    def test_system_view_needs_steps(self) -> None:
        data = example(SYSTEM)
        for n in data["graph"]["nodes"]:
            n.pop("steps", None)
        self.assertTrue(any("have no steps" in w for w in rp.audit(rp.validate(data))))

    def test_empty_and_unwired_pipelines(self) -> None:
        data = example()
        data["pipelines"].append({"id": "ghost", "name": "Ghost", "type": "etl"})
        self.assertTrue(any("has no nodes" in w for w in rp.audit(rp.validate(data))))
        data = example()
        data["graph"]["edges"] = [e for e in data["graph"]["edges"] if not e["from"].startswith(("load", "chunk", "embed-chunks", "ingest"))]
        warnings = "\n".join(rp.audit(rp.validate(data)))
        self.assertIn("no edges", warnings)

    def test_strict_fails(self) -> None:
        data = example()
        del data["graph"]["nodes"][2]["sourceRef"]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "pipeline.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            result = run("--input", str(path), "--strict")
            self.assertEqual(result.returncode, 1)
            self.assertIn("audit failed", result.stderr)


class PrivacyTests(unittest.TestCase):
    def test_refuses_secrets_in_config(self) -> None:
        data = example()
        data["graph"]["nodes"][10]["config"]["api_key"] = "sk-proj-abcdefghijklmnopqrstuvwx1234"
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "pipeline.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            result = run("--input", str(path))
            self.assertEqual(result.returncode, 1)
            self.assertIn("credential-like", result.stderr)
            self.assertFalse((Path(temp) / "index.html").exists())

    def test_hf_token_detected(self) -> None:
        data = rp.validate(example())
        data["graph"]["nodes"][0]["detail"] = "uses hf_" + "a" * 34
        self.assertTrue(rp.find_secrets(data))

    def test_template_is_offline(self) -> None:
        template = TEMPLATE.read_text(encoding="utf-8")
        remote = re.compile(r"<(?:script|img|link|iframe|source)\b[^>]*(?:src|href)\s*=\s*[\"']https?://", re.I)
        self.assertIsNone(remote.search(template))
        self.assertIn("connect-src 'none'", template)
        self.assertIn("default-src 'none'", template)
        for api in ("fetch", "XMLHttpRequest", "WebSocket", "EventSource", "RTCPeerConnection", "sendBeacon"):
            self.assertIn(api, template)
        self.assertNotIn("@import", template)

    def test_dom_children_never_receive_raw_null(self) -> None:
        template = TEMPLATE.read_text(encoding="utf-8")
        for match in re.finditer(r"replaceChildren\((.{0,4})", template):
            self.assertTrue(match.group(1).startswith((")", "...[")), match.group(0))

    def test_escapes_script_boundaries(self) -> None:
        data = example()
        data["project"]["tagline"] = "</script><script>bad()</script>"
        html = rp.render(TEMPLATE, rp.validate(data))
        self.assertNotIn("</script><script>bad()", html)


class RenderAndDiffTests(unittest.TestCase):
    def test_cli_renders_and_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "pipeline.json"
            path.write_text(json.dumps(example()), encoding="utf-8")
            result = run("--input", str(path))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Valid pipeline scan (function view): 2 pipelines, 16 nodes, 16 edges.", result.stdout)
            html = (root / "index.html").read_text(encoding="utf-8")
            self.assertIn("chunk_text()", html)
            self.assertNotIn("__SCAN_MAP_JSON__", html)
            changed = example()
            changed["graph"]["nodes"][3]["steps"].append({"name": "dedupe()", "sourceRef": "chunking.py:60"})
            path.write_text(json.dumps(changed), encoding="utf-8")
            out = run("--input", str(path)).stdout
            self.assertIn("diff: +0 / -0 / ~1", out)
            self.assertTrue((root / "pipeline.previous.json").is_file())

    def test_diff_tracks_pipelines_steps_and_mode(self) -> None:
        prev = rp.validate(example())
        cur = copy.deepcopy(example())
        cur["mode"] = "system"
        cur["pipelines"].append({"id": "eval", "name": "Offline eval", "type": "evaluation"})
        cur["graph"]["nodes"].append({"id": "judge", "label": "LLM judge", "kind": "evaluate", "pipeline": "eval",
                                      "steps": [{"name": "grade()"}]})
        cur["graph"]["nodes"][3]["steps"] = []
        diff = rp.diff_scans(rp.validate(cur), prev)
        self.assertEqual(diff["addedPipelines"], ["eval"])
        self.assertEqual(diff["previousMode"], "function")
        self.assertEqual([n["id"] for n in diff["addedNodes"]], ["judge"])
        self.assertIn("steps", diff["changedNodes"][0]["fields"])


class MeasureTests(unittest.TestCase):
    def _repo(self, files: dict) -> Path:
        temp = Path(tempfile.mkdtemp())
        for rel, text in files.items():
            p = temp / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        return temp

    def test_small_ai_repo_recommends_function_view(self) -> None:
        root = self._repo({
            "app.py": "from openai import OpenAI\nclient = OpenAI()\ndef ask(q):\n    return client.chat.completions.create(model='x', messages=[])\n",
            "store.py": "import chromadb\ndef search(v):\n    pass\n",
            "billing.py": "def charge():\n    pass\n",
            "node_modules/x/index.js": "import OpenAI from 'openai'\n",
            "tests/test_app.py": "from openai import OpenAI\n",
        })
        result = mr.measure(root)
        self.assertEqual(result["recommendedMode"], "function")
        paths = {f["path"] for f in result["topFiles"]}
        self.assertEqual(paths, {"app.py", "store.py"})

    def test_generic_words_are_not_pipeline_signals(self) -> None:
        root = self._repo({"orders.js": "export function retrieveOrder(id) { return embed(id) }\nconst metrics = 1\n"})
        self.assertEqual(mr.measure(root)["pipelineFiles"], 0)

    def test_large_repo_recommends_system_view(self) -> None:
        body = "import torch\n" + "".join(f"def step_{i}(x):\n    return x\n" for i in range(10))
        root = self._repo({f"train/mod_{i}.py": body for i in range(30)})
        self.assertEqual(mr.measure(root)["recommendedMode"], "system")

    def test_reads_notebook_code_cells_only(self) -> None:
        nb = {"cells": [{"cell_type": "markdown", "source": ["import torch"]},
                        {"cell_type": "code", "source": ["x = 1\n"]}]}
        root = self._repo({"explore.ipynb": json.dumps(nb)})
        self.assertEqual(mr.measure(root)["pipelineFiles"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
