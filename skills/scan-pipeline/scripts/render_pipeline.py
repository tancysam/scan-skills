#!/usr/bin/env python3
"""scan-pipeline: validate an agent-authored AI/ML pipeline map, audit it, diff
it against the previous scan, and render one self-contained offline HTML page.

Fully local. Nothing here opens a socket, resolves a domain, or uploads data.
The generated page ships a strict CSP (connect-src 'none', img-src data:) and
disables fetch/XHR/WebSocket/EventSource/sendBeacon at runtime.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

VERSION = 1
FORMAT = "pipeline-scan"
MAX_INPUT_BYTES = 5_000_000
MAX_NODES, MAX_EDGES = 80, 160
MODES = {"system", "function"}
NODE_KINDS = {
    "trigger", "data", "transform", "retrieve", "model", "train", "evaluate",
    "agent", "tool", "store", "output", "monitor", "function",
}
STAGE_KINDS = {"transform", "retrieve", "train", "evaluate", "agent", "function"}
EDGE_KINDS = {"calls", "reads", "writes", "triggers", "feeds", "produces", "loads"}
PIPELINE_TYPES = {
    "training", "fine-tuning", "inference", "batch-inference", "rag", "agent", "etl",
    "feature", "evaluation", "labeling", "streaming", "other",
}
CONFIDENCE = {"high", "medium", "low"}
ID_RE = re.compile(r"^[A-Za-z0-9_.:/-]{1,80}$")
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DOMAIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,118}[A-Za-z0-9])?$")

SECRET_PATTERNS = [
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("AWS access key id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("OpenAI/Anthropic-style key", re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}")),
    ("Hugging Face token", re.compile(r"\bhf_[A-Za-z0-9]{30,}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("Stripe secret key", re.compile(r"\b(?:sk|rk)_live_[0-9A-Za-z]{16,}")),
    ("W&B API key", re.compile(r"\bwandb_v1_[A-Za-z0-9_]{20,}")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    ("credentialed URL", re.compile(r"[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@")),
]

TEMPLATE_NAME = "pipeline-template.html"
ELK_ARCHIVE = "elkjs-0.11.1.tgz"


class ContractError(ValueError):
    pass


# ── primitive validators ──────────────────────────────────────────────────────

def require_dict(value: Any, path: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{path} must be an object")
    return value


def require_list(value: Any, path: str) -> List[Any]:
    if not isinstance(value, list):
        raise ContractError(f"{path} must be an array")
    return value


def require_string(value: Any, path: str, *, minimum: int = 1, maximum: int,
                   pattern: Optional[re.Pattern] = None) -> str:
    if not isinstance(value, str):
        raise ContractError(f"{path} must be a string")
    if not minimum <= len(value) <= maximum:
        raise ContractError(f"{path} must be {minimum}–{maximum} characters (got {len(value)})")
    if pattern and not pattern.fullmatch(value):
        raise ContractError(f"{path} has an invalid format")
    return value


def require_int(value: Any, path: str, maximum: int = 10_000_000) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        raise ContractError(f"{path} must be an integer 0–{maximum}")
    return value


def optional_string(obj: Dict[str, Any], key: str, path: str, maximum: int) -> None:
    if key in obj:
        require_string(obj[key], f"{path}.{key}", minimum=0, maximum=maximum)


def exact_keys(obj: Dict[str, Any], allowed: set, required: set, path: str) -> None:
    missing = required - obj.keys()
    unknown = obj.keys() - allowed
    if missing:
        raise ContractError(f"{path} is missing: {', '.join(sorted(missing))}")
    if unknown:
        raise ContractError(f"{path} has unknown fields: {', '.join(sorted(unknown))}")


def validate_domain(obj: Dict[str, Any], path: str) -> None:
    if "domain" in obj:
        value = require_string(obj["domain"], f"{path}.domain", maximum=120)
        if "://" in value or "/" in value or not DOMAIN_RE.fullmatch(value):
            raise ContractError(f"{path}.domain must be a bare domain like openai.com (no scheme or path)")


def validate_ref(value: Any, path: str) -> str:
    ref = require_string(value, path, maximum=160)
    if ref.startswith("/") or "://" in ref or ".." in ref.split("/") or re.match(r"^[A-Za-z]:\\", ref):
        raise ContractError(f"{path} must be a repository-relative path")
    return ref


def validate_string_list(obj: Dict[str, Any], key: str, path: str, *, max_items: int,
                         max_len: int) -> List[str]:
    if key not in obj:
        return []
    values = require_list(obj[key], f"{path}.{key}")
    if len(values) > max_items:
        raise ContractError(f"{path}.{key} may contain at most {max_items} items")
    out: List[str] = []
    for i, v in enumerate(values):
        s = require_string(v, f"{path}.{key}[{i}]", maximum=max_len)
        if s.lower() in (x.lower() for x in out):
            raise ContractError(f"{path}.{key} contains duplicate value {s!r}")
        out.append(s)
    return out


# ── contract ─────────────────────────────────────────────────────────────────

def validate(data: Any) -> Dict[str, Any]:
    root = require_dict(data, "$")
    exact_keys(root,
               {"version", "format", "project", "mode", "scope", "stats", "frameworks", "pipelines", "graph"},
               {"version", "format", "project", "mode", "stats", "pipelines", "graph"}, "$")
    if root["version"] != VERSION or isinstance(root["version"], bool):
        raise ContractError("$.version must equal 1")
    if root["format"] != FORMAT:
        raise ContractError(f"$.format must equal {FORMAT!r}")
    if root["mode"] not in MODES:
        raise ContractError("$.mode must be 'system' or 'function'")

    project = require_dict(root["project"], "$.project")
    exact_keys(project, {"name", "slug", "tagline", "date"}, {"name", "slug", "date"}, "$.project")
    require_string(project["name"], "$.project.name", maximum=48)
    require_string(project["slug"], "$.project.slug", maximum=48, pattern=SLUG_RE)
    require_string(project["date"], "$.project.date", maximum=10, pattern=DATE_RE)
    optional_string(project, "tagline", "$.project", 100)

    if "scope" in root:
        scope = require_dict(root["scope"], "$.scope")
        exact_keys(scope, {"files", "loc", "pipelineFiles", "focus"}, set(), "$.scope")
        for key in ("files", "loc", "pipelineFiles"):
            if key in scope:
                require_int(scope[key], f"$.scope.{key}")
        if "focus" in scope:
            require_string(scope["focus"], "$.scope.focus", maximum=100)

    stats = require_dict(root["stats"], "$.stats")
    exact_keys(stats, {"pipelines", "stages", "models", "datasets"},
               {"pipelines", "stages", "models", "datasets"}, "$.stats")
    for key, value in stats.items():
        require_int(value, f"$.stats.{key}", 9999)

    if "frameworks" in root:
        items = require_list(root["frameworks"], "$.frameworks")
        if len(items) > 12:
            raise ContractError("$.frameworks may contain at most 12 items")
        seen: set = set()
        for i, raw in enumerate(items):
            p = f"$.frameworks[{i}]"
            item = require_dict(raw, p)
            exact_keys(item, {"id", "label", "domain", "role"}, {"id", "label"}, p)
            fid = require_string(item["id"], f"{p}.id", maximum=80, pattern=ID_RE)
            require_string(item["label"], f"{p}.label", maximum=40)
            optional_string(item, "role", p, 40)
            validate_domain(item, p)
            if fid in seen:
                raise ContractError(f"$.frameworks contains duplicate id {fid!r}")
            seen.add(fid)

    pipelines = require_list(root["pipelines"], "$.pipelines")
    if not 1 <= len(pipelines) <= 12:
        raise ContractError("$.pipelines must contain 1–12 pipelines")
    pipeline_ids: set = set()
    pipeline_names: set = set()
    for i, raw in enumerate(pipelines):
        p = f"$.pipelines[{i}]"
        pipe = require_dict(raw, p)
        exact_keys(pipe, {"id", "name", "type", "trigger", "detail", "sourceRef"}, {"id", "name", "type"}, p)
        pid = require_string(pipe["id"], f"{p}.id", maximum=80, pattern=ID_RE)
        name = require_string(pipe["name"], f"{p}.name", maximum=28)
        if pipe["type"] not in PIPELINE_TYPES:
            raise ContractError(f"{p}.type must be one of: {', '.join(sorted(PIPELINE_TYPES))}")
        optional_string(pipe, "trigger", p, 48)
        optional_string(pipe, "detail", p, 240)
        if "sourceRef" in pipe:
            validate_ref(pipe["sourceRef"], f"{p}.sourceRef")
        if pid in pipeline_ids or name.lower() in pipeline_names:
            raise ContractError(f"{p} duplicates another pipeline id or name")
        pipeline_ids.add(pid)
        pipeline_names.add(name.lower())

    graph = require_dict(root["graph"], "$.graph")
    exact_keys(graph, {"nodes", "edges"}, {"nodes", "edges"}, "$.graph")
    nodes = require_list(graph["nodes"], "$.graph.nodes")
    edges = require_list(graph["edges"], "$.graph.edges")
    if not 1 <= len(nodes) <= MAX_NODES:
        raise ContractError(f"$.graph.nodes must contain 1–{MAX_NODES} nodes")
    if len(edges) > MAX_EDGES:
        raise ContractError(f"$.graph.edges may contain at most {MAX_EDGES} edges")

    node_allowed = {"id", "label", "kind", "sub", "detail", "pipeline", "sourceRef", "framework",
                    "domain", "steps", "config", "capabilities", "confidence"}
    ids: set = set()
    for index, raw in enumerate(nodes):
        path = f"$.graph.nodes[{index}]"
        node = require_dict(raw, path)
        exact_keys(node, node_allowed, {"id", "label", "kind"}, path)
        node_id = require_string(node["id"], f"{path}.id", maximum=80, pattern=ID_RE)
        if node_id in ids:
            raise ContractError(f"duplicate node id {node_id!r}")
        ids.add(node_id)
        require_string(node["label"], f"{path}.label", maximum=32)
        if node["kind"] not in NODE_KINDS:
            raise ContractError(f"{path}.kind must be one of: {', '.join(sorted(NODE_KINDS))}")
        optional_string(node, "sub", path, 56)
        optional_string(node, "detail", path, 240)
        optional_string(node, "framework", path, 32)
        validate_domain(node, path)
        if "pipeline" in node and node["pipeline"] not in pipeline_ids:
            raise ContractError(f"{path}.pipeline {node['pipeline']!r} is not a pipeline id")
        if "sourceRef" in node:
            validate_ref(node["sourceRef"], f"{path}.sourceRef")
        if "steps" in node:
            steps = require_list(node["steps"], f"{path}.steps")
            if len(steps) > 12:
                raise ContractError(f"{path}.steps may contain at most 12 steps")
            for s_i, s_raw in enumerate(steps):
                sp = f"{path}.steps[{s_i}]"
                step = require_dict(s_raw, sp)
                exact_keys(step, {"name", "sourceRef", "note"}, {"name"}, sp)
                require_string(step["name"], f"{sp}.name", maximum=56)
                if "sourceRef" in step:
                    validate_ref(step["sourceRef"], f"{sp}.sourceRef")
                optional_string(step, "note", sp, 80)
        if "config" in node:
            config = require_dict(node["config"], f"{path}.config")
            if len(config) > 8:
                raise ContractError(f"{path}.config may contain at most 8 entries")
            for key, value in config.items():
                require_string(key, f"{path}.config key", maximum=32)
                require_string(value, f"{path}.config.{key}", maximum=64)
        validate_string_list(node, "capabilities", path, max_items=6, max_len=24)
        if "confidence" in node and node["confidence"] not in CONFIDENCE:
            raise ContractError(f"{path}.confidence must be one of: high, medium, low")

    seen_edges: set = set()
    for index, raw in enumerate(edges):
        path = f"$.graph.edges[{index}]"
        edge = require_dict(raw, path)
        exact_keys(edge, {"from", "to", "kind", "label"}, {"from", "to"}, path)
        source = require_string(edge["from"], f"{path}.from", maximum=80, pattern=ID_RE)
        target = require_string(edge["to"], f"{path}.to", maximum=80, pattern=ID_RE)
        if source not in ids or target not in ids:
            missing = source if source not in ids else target
            raise ContractError(f"{path} references a missing node {missing!r}")
        if source == target:
            raise ContractError(f"{path} may not connect a node to itself")
        if "kind" in edge and edge["kind"] not in EDGE_KINDS:
            raise ContractError(f"{path}.kind must be one of: {', '.join(sorted(EDGE_KINDS))}")
        optional_string(edge, "label", path, 32)
        key = (source, target, edge.get("kind"), edge.get("label"))
        if key in seen_edges:
            raise ContractError(f"{path} duplicates an earlier edge")
        seen_edges.add(key)
    return root


# ── audit ────────────────────────────────────────────────────────────────────

def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield str(k)
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def find_secrets(data: Dict[str, Any]) -> List[str]:
    hits: List[str] = []
    for text in _strings(data):
        for name, pattern in SECRET_PATTERNS:
            if pattern.search(text):
                hits.append(f"possible {name} in scan text: {text[:24]!r}…")
    return hits


def audit(data: Dict[str, Any]) -> List[str]:
    """Warnings for maps that are valid but misleading. Loose on purpose."""
    nodes = data["graph"]["nodes"]
    edges = data["graph"]["edges"]
    by_id = {n["id"]: n for n in nodes}
    warnings: List[str] = []
    connected = {e["from"] for e in edges} | {e["to"] for e in edges}
    outgoing: Dict[str, List[Dict[str, Any]]] = {n["id"]: [] for n in nodes}
    incoming: Dict[str, List[Dict[str, Any]]] = {n["id"]: [] for n in nodes}
    for e in edges:
        outgoing[e["from"]].append(e)
        incoming[e["to"]].append(e)

    for pipe in data["pipelines"]:
        members = [n for n in nodes if n.get("pipeline") == pipe["id"]]
        ids = {n["id"] for n in members}
        if not members:
            warnings.append(f"pipeline '{pipe['name']}' has no nodes — tag its stages with \"pipeline\": \"{pipe['id']}\"")
            continue
        if len(members) == 1:
            warnings.append(f"pipeline '{pipe['name']}' has a single node — map its stages, or fold it into another pipeline")
        internal = [e for e in edges if e["from"] in ids and e["to"] in ids]
        if len(members) >= 3 and not internal:
            warnings.append(f"pipeline '{pipe['name']}' has no edges between its stages — wire the flow in order")
        starts = [n for n in members if n["kind"] in {"trigger", "data"}]
        fed = [e for e in edges if e["to"] in ids and e["from"] not in ids]
        if not starts and not fed:
            warnings.append(f"pipeline '{pipe['name']}' has no visible start — add its trigger or input data")
        if pipe["type"] in {"training", "fine-tuning"}:
            if not any(n["kind"] == "evaluate" for n in members) and not any(
                    by_id[e["to"]]["kind"] == "evaluate" for n in members for e in outgoing[n["id"]]):
                warnings.append(f"training pipeline '{pipe['name']}' shows no evaluation — map it if it exists; if it truly doesn't, say so in detail")

    for n in nodes:
        if n["kind"] == "train" and not any(e.get("kind") in {"produces", "writes"} for e in outgoing[n["id"]]):
            warnings.append(f"train node '{n['label']}' produces nothing — add a 'produces' edge to the model/checkpoint it outputs")
        if n["kind"] == "model" and not incoming[n["id"]] and not outgoing[n["id"]]:
            pass  # reported as isolated below
    isolated = [n["label"] for n in nodes if n["id"] not in connected]
    if len(nodes) >= 4 and isolated:
        warnings.append(f"{len(isolated)} node(s) have no edges: {', '.join(isolated[:5])}{'…' if len(isolated) > 5 else ''}")
    if len(nodes) >= 6 and len(edges) * 2 < len(nodes):
        warnings.append(f"only {len(edges)} edges for {len(nodes)} nodes — wire each pipeline end to end")
    if not any(n["kind"] in {"model", "agent", "train"} for n in nodes):
        warnings.append("no model, train or agent nodes — this map shows no AI/ML at all; check the scan scope")

    stage_nodes = [n for n in nodes if n["kind"] in STAGE_KINDS]
    if data["mode"] == "function":
        missing = [n for n in stage_nodes if not n.get("sourceRef")]
        if missing:
            warnings.append(f"function view: {len(missing)} stage node(s) lack sourceRef (file:line): {', '.join(n['label'] for n in missing[:5])}")
    else:
        no_steps = [n for n in stage_nodes if not n.get("steps")]
        if len(stage_nodes) >= 4 and len(no_steps) * 2 > len(stage_nodes):
            warnings.append(f"system view: {len(no_steps)} of {len(stage_nodes)} stages have no steps — list their key functions so readers can drill down")
        if len(nodes) > 50:
            warnings.append(f"system view with {len(nodes)} nodes — abstract further (merge stages, move functions into steps)")

    counts = {
        "pipelines": len(data["pipelines"]),
        "models": sum(1 for n in nodes if n["kind"] == "model"),
        "datasets": sum(1 for n in nodes if n["kind"] == "data"),
    }
    for key, drawn in counts.items():
        if drawn > data["stats"][key]:
            warnings.append(f"stats.{key}={data['stats'][key]} but the map draws {drawn}")
    return warnings


# ── diff ─────────────────────────────────────────────────────────────────────

def _step_names(node: Dict[str, Any]) -> List[str]:
    return [s["name"] for s in node.get("steps", [])]


def diff_scans(current: Dict[str, Any], previous: Dict[str, Any]) -> Dict[str, Any]:
    prev_nodes = {n["id"]: n for n in previous["graph"]["nodes"]}
    cur_nodes = {n["id"]: n for n in current["graph"]["nodes"]}
    brief = lambda n: {"id": n["id"], "label": n["label"], "kind": n["kind"]}  # noqa: E731
    added = [brief(n) for n in current["graph"]["nodes"] if n["id"] not in prev_nodes]
    removed = [brief(n) for n in previous["graph"]["nodes"] if n["id"] not in cur_nodes]
    changed = []
    for n in current["graph"]["nodes"]:
        p = prev_nodes.get(n["id"])
        if not p:
            continue
        fields = [f for f in ("label", "kind", "sub", "pipeline", "sourceRef", "framework", "config")
                  if p.get(f) != n.get(f)]
        if _step_names(p) != _step_names(n):
            fields.append("steps")
        if fields:
            changed.append({**brief(n), "fields": fields})
    ekey = lambda e: (e["from"], e["to"])  # noqa: E731
    prev_edges = {ekey(e) for e in previous["graph"]["edges"]}
    cur_edges = {ekey(e) for e in current["graph"]["edges"]}
    prev_pipes = {p["id"] for p in previous["pipelines"]}
    cur_pipes = {p["id"] for p in current["pipelines"]}
    stat_deltas = {k: current["stats"][k] - previous["stats"].get(k, 0)
                   for k in current["stats"] if current["stats"][k] != previous["stats"].get(k, 0)}
    result = {
        "previousDate": previous["project"]["date"],
        "previousMode": previous["mode"],
        "addedNodes": added,
        "removedNodes": removed,
        "changedNodes": changed,
        "addedEdges": len(cur_edges - prev_edges),
        "removedEdges": len(prev_edges - cur_edges),
        "addedPipelines": sorted(cur_pipes - prev_pipes),
        "removedPipelines": sorted(prev_pipes - cur_pipes),
        "statDeltas": stat_deltas,
    }
    result["hasChanges"] = bool(added or removed or changed or stat_deltas or result["addedEdges"]
                                or result["removedEdges"] or result["addedPipelines"] or result["removedPipelines"])
    return result


# ── IO + render ──────────────────────────────────────────────────────────────

def read_json(path: Path) -> Any:
    if not path.is_file():
        raise ContractError(f"input file does not exist: {path}")
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise ContractError("input JSON exceeds 5 MB")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ContractError(f"invalid JSON in {path.name} at line {exc.lineno}, column {exc.colno}: {exc.msg}") from exc


def load_json(path: Path) -> Dict[str, Any]:
    return validate(read_json(path))


def load_elk(template_path: Path) -> str:
    archive_path = template_path.with_name(ELK_ARCHIVE)
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            source = archive.extractfile(archive.getmember("package/lib/elk.bundled.js"))
            if source is None:
                raise ContractError("ELK bundle is missing from renderer assets")
            return source.read().decode("utf-8")
    except (OSError, tarfile.TarError, UnicodeDecodeError, KeyError) as exc:
        raise ContractError(f"cannot load bundled ELK layout engine: {exc}") from exc


def safe_json(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return (payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def render(template_path: Path, data: Dict[str, Any], *, diff: Optional[Dict[str, Any]] = None,
           warnings: Optional[List[str]] = None) -> str:
    template = template_path.read_text(encoding="utf-8")
    marker, elk_marker = "__SCAN_MAP_JSON__", "__ELKJS__"
    if template.count(marker) != 1 or template.count(elk_marker) != 1:
        raise ContractError("renderer template must contain exactly one JSON and one ELK marker")
    payload = safe_json({"scan": data, "diff": diff, "audit": warnings or []})
    elk_source = load_elk(template_path).replace("</script", "<\\/script")
    head, tail = template.split(elk_marker)
    if marker not in tail:
        raise ContractError("renderer template must place the JSON marker after the ELK marker")
    before, after = tail.split(marker)
    return head + elk_source + before + payload + after


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    temporary.replace(path)


def resolve_previous(input_path: Path, data: Dict[str, Any], explicit: Optional[str],
                     disabled: bool, persist: bool) -> Optional[Dict[str, Any]]:
    """Last rendered scan → pipeline.previous.json when the input changes."""
    if disabled:
        return None
    if explicit:
        return validate(read_json(Path(explicit).expanduser().resolve()))
    rendered = input_path.with_name("pipeline.rendered.json")
    previous = input_path.with_name("pipeline.previous.json")
    last = None
    if rendered.is_file():
        try:
            last = validate(read_json(rendered))
        except ContractError:
            last = None
    if persist:
        if last is not None and last != data:
            atomic_write(previous, json.dumps(last, ensure_ascii=False, indent=2) + "\n")
        atomic_write(rendered, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    if previous.is_file():
        try:
            return validate(read_json(previous))
        except ContractError:
            return None
    return None


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate, audit, diff and render a local AI/ML pipeline scan.")
    parser.add_argument("--input", required=True, help="Path to pipeline.json")
    parser.add_argument("--output", help="HTML output path (default: index.html next to the input)")
    parser.add_argument("--validate-only", action="store_true", help="Validate and audit without rendering")
    parser.add_argument("--strict", action="store_true", help="Treat audit warnings as errors")
    parser.add_argument("--previous", help="Diff against this scan instead of the saved snapshot")
    parser.add_argument("--no-diff", action="store_true", help="Do not show a 'Since last scan' card")
    return parser.parse_args(argv)


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = parse_args(argv)
    input_path = Path(args.input).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve() if args.output else input_path.with_name("index.html")
    template_path = Path(__file__).resolve().parent.parent / "assets" / TEMPLATE_NAME
    try:
        data = load_json(input_path)
        secrets = find_secrets(data)
        if secrets:
            raise ContractError("refusing to render — scan contains credential-like text:\n  " + "\n  ".join(secrets))
        warnings = audit(data)
        if args.strict and warnings:
            raise ContractError("audit failed (--strict):\n  " + "\n  ".join(warnings))
        previous = resolve_previous(input_path, data, args.previous, args.no_diff,
                                    persist=not args.validate_only)
        diff = diff_scans(data, previous) if previous is not None else None
        if diff and not diff["hasChanges"]:
            diff = None
        if not args.validate_only:
            atomic_write(output_path, render(template_path, data, diff=diff, warnings=warnings))
    except (OSError, ContractError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    graph = data["graph"]
    print(f"Valid pipeline scan ({data['mode']} view): {len(data['pipelines'])} pipelines, "
          f"{len(graph['nodes'])} nodes, {len(graph['edges'])} edges.")
    for warning in warnings:
        print(f"audit: {warning}")
    if diff:
        print(f"diff: +{len(diff['addedNodes'])} / -{len(diff['removedNodes'])} / ~{len(diff['changedNodes'])} nodes since {diff['previousDate']}")
    if not args.validate_only:
        print(f"HTML: {output_path}")
    print("Privacy: local render only; no network access or repository transmission.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
