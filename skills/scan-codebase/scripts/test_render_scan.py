#!/usr/bin/env python3
"""Tests for the JSON contract and offline renderer."""

from __future__ import annotations

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
RENDERER = SCRIPT_DIR / "render_scan.py"
TEMPLATE = SKILL_DIR / "assets" / "scan-template.html"
EXAMPLE = SKILL_DIR / "references" / "example-scan.json"

spec = importlib.util.spec_from_file_location("render_scan", RENDERER)
render_scan = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = render_scan
spec.loader.exec_module(render_scan)


def sample_scan() -> dict:
    return {
        "version": 1,
        "project": {
            "name": "Unfamiliar Systems",
            "slug": "unfamiliar-systems",
            "tagline": "Exercises arbitrary repository-derived technologies",
            "date": "2026-07-26",
        },
        "stats": {"agents": 1, "models": 1, "tools": 1, "integrations": 3},
        "topModels": [{"id": "provider-model", "label": "NovaMind Q7"}],
        "topTools": [{"id": "provider-tool", "label": "GlyphProbe Search"}],
        "topIntegrations": [
            {"id": "queue", "label": "ChronoFlux Mesh"},
            {"id": "nebula", "label": "VectorNebula DB"},
        ],
        "graph": {
            "nodes": [
                {
                    "id": "route",
                    "label": "Prediction route",
                    "kind": "entry",
                    "sub": "POST /predict",
                    "group": "Predictions",
                    "sourceRef": "src/routes/predict.ts:18",
                    "detail": "Accepts prediction work.",
                },
                {
                    "id": "service",
                    "label": "Prediction service",
                    "kind": "service",
                    "sub": "builds the request",
                    "group": "Predictions",
                    "sourceRef": "src/predictions/service.ts:22",
                },
                {
                    "id": "agent",
                    "label": "Forecast agent",
                    "kind": "agent",
                    "sub": "orchestrates inference",
                    "group": "Predictions",
                    "sourceRef": "src/agents/forecast.ts:9",
                },
                {"id": "model", "label": "NovaMind Q7", "kind": "model"},
                {"id": "tool", "label": "GlyphProbe Search", "kind": "tool"},
                {
                    "id": "queue",
                    "label": "ChronoFlux Mesh",
                    "kind": "external",
                    "sub": "prediction jobs",
                    "group": "Processing",
                },
                {
                    "id": "worker",
                    "label": "Prediction worker",
                    "kind": "service",
                    "sub": "consumes jobs",
                    "group": "Processing",
                    "sourceRef": "src/workers/predict.ts:12",
                },
                {
                    "id": "nebula",
                    "label": "VectorNebula DB",
                    "kind": "store",
                    "sub": "vectors + forecasts",
                    "group": "Storage",
                },
                {
                    "id": "signal",
                    "label": "SignalGlass API",
                    "kind": "external",
                    "sub": "market observations",
                    "group": "Integrations",
                },
                {
                    "id": "cron",
                    "label": "Refresh trigger",
                    "kind": "cron",
                    "sub": "every 15 minutes",
                    "group": "Processing",
                },
            ],
            "edges": [
                {"from": "route", "to": "service", "kind": "triggers", "label": "validated request"},
                {"from": "service", "to": "agent", "kind": "calls", "label": "builds forecast"},
                {"from": "agent", "to": "model", "kind": "calls"},
                {"from": "agent", "to": "tool", "kind": "calls"},
                {"from": "service", "to": "queue", "kind": "writes", "label": "enqueues work"},
                {"from": "cron", "to": "queue", "kind": "writes", "label": "refresh batch"},
                {"from": "queue", "to": "worker", "kind": "triggers", "label": "delivers job"},
                {"from": "worker", "to": "nebula", "kind": "writes", "label": "stores forecast"},
                {"from": "worker", "to": "signal", "kind": "calls", "label": "reads observations"},
            ],
        },
    }


class RenderScanTests(unittest.TestCase):
    def test_accepts_arbitrary_technology_labels(self) -> None:
        data = render_scan.validate(sample_scan())
        labels = {node["label"] for node in data["graph"]["nodes"]}
        self.assertIn("ChronoFlux Mesh", labels)
        self.assertIn("VectorNebula DB", labels)
        html = render_scan.render(TEMPLATE, data)
        self.assertIn("ChronoFlux Mesh", html)
        self.assertIn("VectorNebula DB", html)
        self.assertNotIn("__SCAN_MAP_JSON__", html)

    def test_cli_renders_next_to_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            input_path = root / "scan.json"
            input_path.write_text(json.dumps(sample_scan()), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(RENDERER), "--input", str(input_path)],
                capture_output=True,
                text=True,
                check=False,
                timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Valid scan v1: 10 nodes, 9 edges, 4 named groups.", result.stdout)
            html_path = root / "index.html"
            self.assertTrue(html_path.is_file())
            html = html_path.read_text(encoding="utf-8")
            self.assertIn("connect-src 'none'", html)
            self.assertIn("NovaMind Q7", html)

    def test_rejects_dangling_edges(self) -> None:
        data = sample_scan()
        data["graph"]["edges"][0]["to"] = "missing"
        with self.assertRaisesRegex(render_scan.ContractError, "missing node"):
            render_scan.validate(data)

    def test_enforces_original_scan_caps(self) -> None:
        data = sample_scan()
        data["topModels"] = data["topModels"] * 4
        with self.assertRaisesRegex(render_scan.ContractError, "topModels may contain at most 3"):
            render_scan.validate(data)

    def test_rejects_kinds_outside_the_original_contract(self) -> None:
        data = sample_scan()
        data["graph"]["nodes"][5]["kind"] = "queue"
        with self.assertRaisesRegex(render_scan.ContractError, "kind must be one of"):
            render_scan.validate(data)

    def test_escapes_embedded_script_boundaries(self) -> None:
        data = sample_scan()
        data["project"]["tagline"] = "</script><script>bad()</script>"
        html = render_scan.render(TEMPLATE, render_scan.validate(data))
        self.assertNotIn("</script><script>bad()", html)
        self.assertIn("\\u003c/script\\u003e", html)

    def test_template_has_no_remote_resources(self) -> None:
        template = TEMPLATE.read_text(encoding="utf-8")
        remote_tag = re.compile(
            r"<(?:script|img|link|iframe|source)\b[^>]*(?:src|href)\s*=\s*[\"']https?://",
            re.I,
        )
        self.assertIsNone(remote_tag.search(template))
        self.assertNotIn("@import", template)
        self.assertNotIn("url(http", template.lower())
        self.assertIn("connect-src 'none'", template)
        self.assertIn("Network access is disabled", template)

    # ── v2 local contract ────────────────────────────────────────────────
    def test_example_v2_scan_is_valid_and_clean(self) -> None:
        data = render_scan.validate(json.loads(EXAMPLE.read_text(encoding="utf-8")))
        self.assertEqual(data["version"], 2)
        self.assertEqual(render_scan.audit(data), [])
        self.assertEqual(render_scan.find_secrets(data), [])
        html = render_scan.render(TEMPLATE, data)
        self.assertIn("Nightly KB refresh", html)

    def test_v1_rejects_v2_only_fields(self) -> None:
        data = sample_scan()
        data["graph"]["nodes"][2]["capabilities"] = ["tool loop"]
        with self.assertRaisesRegex(render_scan.ContractError, "unknown fields: capabilities"):
            render_scan.validate(data)

    def test_v2_capabilities_confidence_and_workflows(self) -> None:
        data = sample_scan()
        data["version"] = 2
        data["workflows"] = ["Processing"]
        data["graph"]["nodes"][2]["capabilities"] = ["tool loop", "streaming"]
        data["graph"]["nodes"][2]["confidence"] = "medium"
        render_scan.validate(data)
        data["graph"]["nodes"][2]["confidence"] = "certain"
        with self.assertRaisesRegex(render_scan.ContractError, "confidence"):
            render_scan.validate(data)

    def test_workflows_must_be_groups_not_nodes(self) -> None:
        data = sample_scan()
        data["version"] = 2
        data["workflows"] = ["Prediction worker"]
        with self.assertRaisesRegex(render_scan.ContractError, "must match a node group"):
            render_scan.validate(data)

    def test_rejects_domains_with_scheme_and_absolute_source_refs(self) -> None:
        data = sample_scan()
        data["topModels"][0]["domain"] = "https://evil.example/x"
        with self.assertRaisesRegex(render_scan.ContractError, "bare domain"):
            render_scan.validate(data)
        data = sample_scan()
        data["graph"]["nodes"][0]["sourceRef"] = "/etc/passwd"
        with self.assertRaisesRegex(render_scan.ContractError, "repository-relative"):
            render_scan.validate(data)

    # ── audit ────────────────────────────────────────────────────────────
    def test_audit_flags_degenerate_maps(self) -> None:
        data = sample_scan()
        data["graph"]["nodes"] = [
            {"id": f"a{i}", "label": f"Agent {i}", "kind": "agent"} for i in range(9)
        ] + [{"id": "m", "label": "Model", "kind": "model"}]
        data["graph"]["edges"] = [{"from": "a0", "to": "m"}]
        warnings = "\n".join(render_scan.audit(data))
        self.assertIn("nodes are agents", warnings)
        self.assertIn("edges for", warnings)
        self.assertIn("agents have no edges", warnings)
        self.assertIn("no entry or cron nodes", warnings)

    def test_strict_mode_fails_on_audit_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "scan.json"
            path.write_text(json.dumps(sample_scan()), encoding="utf-8")
            result = subprocess.run([sys.executable, str(RENDERER), "--input", str(path), "--strict"],
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 1)
            self.assertIn("audit failed", result.stderr)

    # ── privacy ──────────────────────────────────────────────────────────
    def test_refuses_to_render_credentials(self) -> None:
        data = sample_scan()
        data["graph"]["nodes"][0]["detail"] = "uses key sk-ant-abcdefghijklmnopqrstuvwxyz0123"
        self.assertTrue(render_scan.find_secrets(render_scan.validate(data)))
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "scan.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            result = subprocess.run([sys.executable, str(RENDERER), "--input", str(path)],
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 1)
            self.assertIn("credential-like", result.stderr)
            self.assertFalse((Path(temp) / "index.html").exists())

    def test_template_blocks_runtime_network_apis(self) -> None:
        template = TEMPLATE.read_text(encoding="utf-8")
        for api in ("fetch", "XMLHttpRequest", "WebSocket", "EventSource", "RTCPeerConnection", "sendBeacon"):
            self.assertIn(api, template)
        self.assertIn("default-src 'none'", template)
        self.assertNotIn("innerHTML=`", template)

    # ── diff ─────────────────────────────────────────────────────────────
    def test_diff_reports_added_removed_changed(self) -> None:
        previous = render_scan.validate(sample_scan())
        current = sample_scan()
        current["graph"]["nodes"] = [n for n in current["graph"]["nodes"] if n["id"] != "signal"]
        current["graph"]["edges"] = [e for e in current["graph"]["edges"] if e["to"] != "signal"]
        current["graph"]["nodes"].append({"id": "audit", "label": "Audit log", "kind": "store"})
        current["graph"]["edges"].append({"from": "worker", "to": "audit", "kind": "writes"})
        current["graph"]["nodes"][2]["sub"] = "now streams"
        current["stats"]["integrations"] = 4
        diff = render_scan.diff_scans(render_scan.validate(current), previous)
        self.assertTrue(diff["hasChanges"])
        self.assertEqual([n["id"] for n in diff["addedNodes"]], ["audit"])
        self.assertEqual([n["id"] for n in diff["removedNodes"]], ["signal"])
        self.assertEqual(diff["changedNodes"][0]["fields"], ["sub"])
        self.assertEqual(diff["statDeltas"], {"integrations": 1})
        self.assertEqual((diff["addedEdges"], diff["removedEdges"]), (1, 1))

    def test_rerender_keeps_previous_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "scan.json"
            run = lambda: subprocess.run([sys.executable, str(RENDERER), "--input", str(path)],
                                         capture_output=True, text=True, timeout=20)
            path.write_text(json.dumps(sample_scan()), encoding="utf-8")
            self.assertNotIn("diff:", run().stdout)
            changed = sample_scan()
            changed["graph"]["nodes"][0]["label"] = "Forecast route"
            path.write_text(json.dumps(changed), encoding="utf-8")
            self.assertIn("diff: +0 / -0 / ~1", run().stdout)
            self.assertIn("diff: +0 / -0 / ~1", run().stdout)  # unchanged re-render keeps the diff
            self.assertTrue((root / "scan.previous.json").is_file())

    def test_dom_children_never_receive_raw_null(self) -> None:
        # replaceChildren(null) renders the text "null" — every call must be
        # empty or spread a filtered array.
        template = TEMPLATE.read_text(encoding="utf-8")
        for match in re.finditer(r"replaceChildren\((.{0,4})", template):
            self.assertTrue(match.group(1).startswith((")", "...[")), match.group(0))
        self.assertIn("].filter(Boolean))", template)


if __name__ == "__main__":
    unittest.main(verbosity=2)
