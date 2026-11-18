#!/usr/bin/env python3
"""scan-codebase: validate an agent-authored scan, audit it, diff it against the
previous scan, and render one self-contained offline HTML map.

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

SUPPORTED_VERSIONS = (1, 2)
MAX_INPUT_BYTES = 5_000_000
NODE_KINDS = {"entry", "cron", "agent", "model", "tool", "service", "store", "external"}
EDGE_KINDS = {"calls", "reads", "writes", "triggers"}
CONFIDENCE = {"high", "medium", "low"}
ID_RE = re.compile(r"^[A-Za-z0-9_.:/-]{1,80}$")
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DOMAIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,118}[A-Za-z0-9])?$")

# Strings that look like credentials. A scan is architecture metadata; any of
# these appearing in it means something was copied that should not have been.
SECRET_PATTERNS = [
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("AWS access key id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("OpenAI/Anthropic-style key", re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("Stripe secret key", re.compile(r"\b(?:sk|rk)_live_[0-9A-Za-z]{16,}")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    ("credentialed URL", re.compile(r"[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@")),
]

TEMPLATE_NAME = "scan-template.html"
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


def optional_string(obj: Dict[str, Any], key: str, path: str, maximum: int,
                    pattern: Optional[re.Pattern] = None) -> None:
    if key in obj:
        require_string(obj[key], f"{path}.{key}", minimum=0 if pattern is None else 1,
                       maximum=maximum, pattern=pattern)


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


def validate_rail(items: Any, path: str, maximum_items: int) -> None:
    values = require_list(items, path)
    if len(values) > maximum_items:
        raise ContractError(f"{path} may contain at most {maximum_items} items")
    seen: set = set()
    for index, raw in enumerate(values):
        item_path = f"{path}[{index}]"
        item = require_dict(raw, item_path)
        exact_keys(item, {"id", "label", "domain"}, {"id", "label"}, item_path)
        item_id = require_string(item["id"], f"{item_path}.id", maximum=80, pattern=ID_RE)
        require_string(item["label"], f"{item_path}.label", maximum=56)
        validate_domain(item, item_path)
        if item_id in seen:
            raise ContractError(f"{path} contains duplicate id {item_id!r}")
        seen.add(item_id)


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
    """Validate a v1 (upstream-compatible) or v2 (local extensions) scan."""
    root = require_dict(data, "$")
    version = root.get("version")
    if version not in SUPPORTED_VERSIONS or isinstance(version, bool):
        raise ContractError("$.version must be 1 or 2")
    v2 = version == 2

    top_allowed = {"version", "project", "stats", "topModels", "topTools", "topIntegrations", "graph"}
    if v2:
        top_allowed |= {"workflows"}
    exact_keys(root, top_allowed,
               {"version", "project", "stats", "topModels", "topTools", "topIntegrations", "graph"}, "$")

    project = require_dict(root["project"], "$.project")
    exact_keys(project, {"name", "slug", "tagline", "iconDomain", "date"}, {"name", "slug", "date"}, "$.project")
    require_string(project["name"], "$.project.name", maximum=48)
    require_string(project["slug"], "$.project.slug", maximum=48, pattern=SLUG_RE)
    require_string(project["date"], "$.project.date", maximum=10, pattern=DATE_RE)
    optional_string(project, "tagline", "$.project", 80)
    optional_string(project, "iconDomain", "$.project", 120)

    stats = require_dict(root["stats"], "$.stats")
    exact_keys(stats, {"agents", "models", "tools", "integrations"},
               {"agents", "models", "tools", "integrations"}, "$.stats")
    for key, value in stats.items():
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 9999:
            raise ContractError(f"$.stats.{key} must be an integer 0–9999")

    validate_rail(root["topModels"], "$.topModels", 3)
    validate_rail(root["topTools"], "$.topTools", 10)
    validate_rail(root["topIntegrations"], "$.topIntegrations", 10)

    graph = require_dict(root["graph"], "$.graph")
    exact_keys(graph, {"nodes", "edges"}, {"nodes", "edges"}, "$.graph")
    nodes = require_list(graph["nodes"], "$.graph.nodes")
    edges = require_list(graph["edges"], "$.graph.edges")
    if not 1 <= len(nodes) <= 60:
        raise ContractError("$.graph.nodes must contain 1–60 nodes")
    if len(edges) > 120:
        raise ContractError("$.graph.edges may contain at most 120 edges")

    node_allowed = {"id", "label", "kind", "sub", "domain", "detail", "group", "sourceRef"}
    if v2:
        node_allowed |= {"capabilities", "confidence"}
    ids: set = set()
    groups: set = set()
    for index, raw in enumerate(nodes):
        path = f"$.graph.nodes[{index}]"
        node = require_dict(raw, path)
        exact_keys(node, node_allowed, {"id", "label", "kind"}, path)
        node_id = require_string(node["id"], f"{path}.id", maximum=80, pattern=ID_RE)
        if node_id in ids:
            raise ContractError(f"duplicate node id {node_id!r}")
        ids.add(node_id)
        require_string(node["label"], f"{path}.label", maximum=28)
        kind = require_string(node["kind"], f"{path}.kind", maximum=20)
        if kind not in NODE_KINDS:
            raise ContractError(f"{path}.kind must be one of: {', '.join(sorted(NODE_KINDS))}")
        optional_string(node, "sub", path, 40)
        validate_domain(node, path)
        optional_string(node, "detail", path, 200)
        if "group" in node:
            groups.add(require_string(node["group"], f"{path}.group", maximum=24))
        if "sourceRef" in node:
            ref = require_string(node["sourceRef"], f"{path}.sourceRef", maximum=120)
            if ref.startswith("/") or "://" in ref or ".." in ref.split("/"):
                raise ContractError(f"{path}.sourceRef must be a repository-relative path")
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
        if "kind" in edge:
            ekind = require_string(edge["kind"], f"{path}.kind", maximum=20)
            if ekind not in EDGE_KINDS:
                raise ContractError(f"{path}.kind must be one of: {', '.join(sorted(EDGE_KINDS))}")
        optional_string(edge, "label", path, 24)
        key = (source, target, edge.get("kind"), edge.get("label"))
        if key in seen_edges:
            raise ContractError(f"{path} duplicates an earlier edge")
        seen_edges.add(key)

    if v2:
        workflows = validate_string_list(root, "workflows", "$", max_items=12, max_len=24)
        lowered = {g.lower() for g in groups}
        for i, name in enumerate(workflows):
            if name.lower() not in lowered:
                raise ContractError(
                    f"$.workflows[{i}] {name!r} must match a node group (workflows are groups, never nodes)")
    return root


# ── audit: structural quality checks beyond the schema ───────────────────────

def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
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
    """Return human-readable warnings for maps that are valid but degenerate.

    Thresholds are loose on purpose: a tiny repo with two agents and one route
    passes untouched; only maps that are both sizable and degenerate warn.
    """
    nodes = data["graph"]["nodes"]
    edges = data["graph"]["edges"]
    warnings: List[str] = []
    of_kind = lambda k: [n for n in nodes if n["kind"] == k]  # noqa: E731
    agents, models = of_kind("agent"), of_kind("model")
    connected = {e["from"] for e in edges} | {e["to"] for e in edges}

    if agents and not models:
        warnings.append("no model nodes — add one model node per distinct model, wired from the agents that call it")
    if len(nodes) >= 8 and len(agents) / len(nodes) > 0.8:
        warnings.append(f"{len(agents)} of {len(nodes)} nodes are agents — add the models, services, stores and APIs the flows touch")
    if len(nodes) >= 6 and len(edges) * 2 < len(nodes):
        warnings.append(f"only {len(edges)} edges for {len(nodes)} nodes — wire each flow end to end, entry through model")
    if len(nodes) >= 6:
        stranded = [n for n in agents if n["id"] not in connected]
        if len(agents) >= 3 and len(stranded) * 2 > len(agents):
            warnings.append(f"{len(stranded)} of {len(agents)} agents have no edges — connect each FROM its trigger and TO what it uses")
        if not of_kind("entry") and not of_kind("cron"):
            warnings.append("no entry or cron nodes — add the real routes, pages, webhooks, CLIs or jobs that start the work")
    isolated = [n["label"] for n in nodes if n["id"] not in connected]
    if len(nodes) >= 4 and isolated:
        warnings.append(f"{len(isolated)} node(s) have no edges: {', '.join(isolated[:5])}{'…' if len(isolated) > 5 else ''}")
    unused_models = [n["label"] for n in models + of_kind("tool")
                     if not any(e["to"] == n["id"] for e in edges)]
    if unused_models:
        warnings.append(f"model/tool node(s) nobody calls (they cannot fold into an agent card): {', '.join(unused_models[:5])}")
    hubs = [n for n in nodes if n["label"].strip().lower() in {"app", "backend", "server", "frontend", "api"}]
    if len(nodes) >= 8 and hubs:
        degree = {n["id"]: 0 for n in nodes}
        for e in edges:
            degree[e["from"]] += 1
            degree[e["to"]] += 1
        for hub in hubs:
            if degree[hub["id"]] >= max(6, len(edges) // 3):
                warnings.append(f"'{hub['label']}' is a generic hub with {degree[hub['id']]} edges — split it into the concrete routes/pages/jobs")
    if len(agents) > data["stats"]["agents"]:
        warnings.append(f"stats.agents={data['stats']['agents']} but the graph has {len(agents)} agent nodes")
    if len(models) > data["stats"]["models"]:
        warnings.append(f"stats.models={data['stats']['models']} but the graph has {len(models)} model nodes")
    groups: Dict[str, int] = {}
    for n in nodes:
        if n.get("group"):
            groups[n["group"]] = groups.get(n["group"], 0) + 1
    singletons = [g for g, c in groups.items() if c == 1]
    if singletons:
        warnings.append(f"group(s) with a single node read as noise: {', '.join(singletons[:5])}")
    if len(groups) > 8:
        warnings.append(f"{len(groups)} groups — the map reads best with 2–6 feature groups; leave hubs ungrouped")
    internal = [n for n in nodes if n["kind"] in {"agent", "service", "entry", "cron"}]
    if len(internal) >= 4 and sum(1 for n in internal if n.get("sourceRef")) * 2 < len(internal):
        warnings.append("fewer than half of internal nodes have sourceRef — add file:line so readers can jump to code")
    return warnings


# ── diff: "Since last scan" ──────────────────────────────────────────────────

_DIFF_FIELDS = ("label", "kind", "sub", "group", "sourceRef", "detail")


def diff_scans(current: Dict[str, Any], previous: Dict[str, Any]) -> Dict[str, Any]:
    prev_nodes = {n["id"]: n for n in previous["graph"]["nodes"]}
    cur_nodes = {n["id"]: n for n in current["graph"]["nodes"]}
    brief = lambda n: {"id": n["id"], "label": n["label"], "kind": n["kind"]}  # noqa: E731
    added = [brief(n) for n in current["graph"]["nodes"] if n["id"] not in prev_nodes]
    removed = [brief(n) for n in previous["graph"]["nodes"] if n["id"] not in cur_nodes]
    changed = []
    for n in current["graph"]["nodes"]:
        p = prev_nodes.get(n["id"])
        if p:
            fields = [f for f in _DIFF_FIELDS if p.get(f) != n.get(f)]
            if fields:
                changed.append({**brief(n), "fields": fields})
    ekey = lambda e: (e["from"], e["to"])  # noqa: E731
    prev_edges = {ekey(e) for e in previous["graph"]["edges"]}
    cur_edges = {ekey(e) for e in current["graph"]["edges"]}
    stat_deltas = {k: current["stats"][k] - previous["stats"].get(k, 0)
                   for k in current["stats"] if current["stats"][k] != previous["stats"].get(k, 0)}
    result = {
        "previousDate": previous["project"]["date"],
        "addedNodes": added,
        "removedNodes": removed,
        "changedNodes": changed,
        "addedEdges": len(cur_edges - prev_edges),
        "removedEdges": len(prev_edges - cur_edges),
        "statDeltas": stat_deltas,
    }
    result["hasChanges"] = bool(added or removed or changed or stat_deltas
                                or result["addedEdges"] or result["removedEdges"])
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
    # Substitute each marker exactly once, in its own segment, so neither the
    # ELK source nor scan text can ever be re-scanned for a marker.
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
    """Find the scan to diff against.

    Local stand-in for the hosted "previous_data" column: the last rendered scan
    is kept next to the input as scan.rendered.json. When the input differs from
    it, that snapshot becomes scan.previous.json. Re-rendering an unchanged scan
    keeps showing the same diff.
    """
    if disabled:
        return None
    if explicit:
        return validate(read_json(Path(explicit).expanduser().resolve()))
    rendered = input_path.with_name("scan.rendered.json")
    previous = input_path.with_name("scan.previous.json")
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
    parser = argparse.ArgumentParser(description="Validate, audit, diff and render a local codebase scan.")
    parser.add_argument("--input", required=True, help="Path to scan.json")
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
        diff = None
        previous = resolve_previous(input_path, data, args.previous, args.no_diff,
                                    persist=not args.validate_only)
        if previous is not None:
            diff = diff_scans(data, previous)
            if not diff["hasChanges"]:
                diff = None
        if not args.validate_only:
            atomic_write(output_path, render(template_path, data, diff=diff, warnings=warnings))
    except (OSError, ContractError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    graph = data["graph"]
    groups = len({n["group"] for n in graph["nodes"] if n.get("group")})
    print(f"Valid scan v{data['version']}: {len(graph['nodes'])} nodes, {len(graph['edges'])} edges, {groups} named groups.")
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
