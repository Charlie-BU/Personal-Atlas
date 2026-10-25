#!/usr/bin/env python3
"""Personal Atlas: in-memory validation and change preparation; no remote transport."""
import argparse
import base64
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import sys
import uuid
from collections.abc import Mapping

sys.dont_write_bytecode = True
class AtlasError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise AtlasError(message)

VERSION = 1  # Record/graph schema; connection configuration is external.
SKILL = Path(__file__).resolve().parents[1]
ATLAS_PREFIX = "atlas/"
SKILL_PREFIX = "skill/personal-atlas/"
ID = re.compile(r"^[a-z][a-z0-9_-]{0,95}$")
KINDS = {"person", "organization", "place", "profile", "topic", "preference",
         "routine", "state", "event", "decision", "goal", "project", "asset", "source"}
STATUSES = {"active", "historical", "disputed", "retracted"}
RELATIONS = {"about", "part_of", "participated_in", "located_at", "works_at",
             "knows", "supports", "contradicts", "supersedes", "derived_from",
             "depends_on", "related_to"}
DOMAINS = frozenset({"identity", "family", "education", "work", "technology",
                 "health", "finance", "travel", "culture", "communication", "devices",
                 "food", "language", "life", "psychology", "sport", "values"})
CANONICAL = ("records/", "sources/", "operations/", SKILL_PREFIX)
GENERATED = ("graph/", "manifests/")
MAX_SOURCE_BYTES = 20 * 1024 * 1024


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def uid(prefix):
    return prefix + "_" + uuid.uuid4().hex


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def data_bytes(value):
    require(isinstance(value, bytes), "Personal data must remain in memory")
    return value


def digest(value):
    return hashlib.sha256(data_bytes(value)).hexdigest()


def owned(path):
    return path in {"vault.json", ".gitignore"} or path.startswith(CANONICAL + GENERATED)


def canonical(files):
    return {p: files[p] for p in files if not p.startswith(GENERATED)}


def parse_markdown(value):
    text = data_bytes(value).decode("utf-8")
    require(text.startswith("---\n"), "Record needs JSON-compatible YAML frontmatter")
    parts = text.split("\n---\n", 1)
    require(len(parts) == 2, "Unclosed record frontmatter")
    try:
        meta = json.loads(parts[0][4:])
    except ValueError as e:
        raise AtlasError("Frontmatter must be a JSON object (a YAML subset): " + str(e))
    require(isinstance(meta, dict), "Frontmatter must be an object")
    return meta, parts[1].strip() + "\n"


def markdown(meta, body):
    return b"---\n" + encoded(meta) + b"---\n\n" + body.strip().encode() + b"\n"


def date_value(value):
    if value is None:
        return None
    require(isinstance(value, str), "Temporal values must be strings or null")
    if "T" in value:
        try:
            stamp = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise AtlasError("Invalid timestamp: " + value)
        require(stamp.tzinfo is not None, "Semantic timestamps must include an offset")
        return value
    require(re.fullmatch(r"\d{4}(?:-\d{2}(?:-\d{2})?)?", value),
            "Semantic dates use YYYY, YYYY-MM or YYYY-MM-DD; do not invent precision")
    try:
        dt.date.fromisoformat(value + {4: "-01-01", 7: "-01", 10: ""}[len(value)])
    except ValueError:
        raise AtlasError("Invalid semantic date: " + value)
    return value


def date_bounds(value):
    """Calendar bounds for filtering only, never stored as invented event dates."""
    import calendar
    date_value(value)
    if "T" in value:
        stamp = dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(dt.timezone.utc)
        return stamp.timestamp(), stamp.timestamp()
    if len(value) == 4:
        lower, upper = value + "-01-01", value + "-12-31"
    elif len(value) == 7:
        year, month = map(int, value.split("-"))
        lower, upper = value + "-01", value + "-" + str(calendar.monthrange(year, month)[1])
    else:
        lower = upper = value
    start = dt.datetime.fromisoformat(lower).replace(tzinfo=dt.timezone.utc).timestamp()
    end = dt.datetime.fromisoformat(upper).replace(tzinfo=dt.timezone.utc).timestamp() + 86400 - .000001
    return start, end


def temporal_check(meta):
    temporal = meta.get("temporal")
    require(isinstance(temporal, dict), "Every record declares temporal.mode")
    mode = temporal.get("mode")
    require(mode in {"none", "point", "interval"}, "Invalid temporal mode")
    keys = {"none": {"mode"}, "point": {"mode", "at", "precision_note"},
            "interval": {"mode", "start", "end", "precision_note"}}[mode]
    require(not set(temporal) - keys, "Unexpected temporal fields for " + mode)
    if mode == "none":
        require(meta["type"] not in {"event", "decision", "state"},
                "Events/decisions/states require semantic time; use null if unknown")
    if mode == "point":
        require("at" in temporal, "Point time requires at (null allowed)")
        date_value(temporal["at"])
    if mode == "interval":
        require("start" in temporal and "end" in temporal, "Interval requires start/end")
        start, end = date_value(temporal["start"]), date_value(temporal["end"])
        if start and end:
            # Reject only definitely reversed intervals; preserve partial-date uncertainty.
            require(date_bounds(start)[0] <= date_bounds(end)[1],
                    "Interval ends before it starts")


def record_date(meta):
    t = meta["temporal"]
    return t.get("at") if t["mode"] == "point" else t.get("start")


def time_key(meta):
    value = record_date(meta)
    # Partial dates share a calendar bucket; precision is exposed, never fabricated.
    return (value is not None, date_bounds(value)[0] if value else float("-inf"), meta["id"])


def record_path(meta):
    return "records/{}/{}.md".format(meta["type"], meta["id"])


def records_from(files):
    records = {}
    for path, value in files.items():
        if not path.startswith("records/"):
            continue
        require(path.endswith(".md"), "Records must be Markdown: " + path)
        meta, body = parse_markdown(value)
        for field in ("id", "type", "title", "summary", "domains", "temporal", "status",
                      "basis", "created_at", "updated_at"):
            require(field in meta, "Missing record field: " + field + " in " + path)
        rid = meta["id"]
        require(isinstance(rid, str) and ID.fullmatch(rid), "Invalid record ID")
        require(not rid.startswith("domain--"), "Record ID prefix domain-- is reserved for domain nodes")
        require(rid not in records, "Duplicate record ID: " + rid)
        require(meta["type"] in KINDS and path == record_path(meta), "Wrong record type/path")
        require(meta["status"] in STATUSES, "Invalid status")
        require(meta["basis"] in {"user_statement", "document", "third_party", "inference", "navigation"},
                "Invalid evidence basis")
        for field in ("title", "summary"):
            require(isinstance(meta[field], str) and meta[field].strip(), "Empty " + field)
        require(len(meta["summary"]) <= 500 and len(meta["title"]) <= 160,
                "Keep title <=160 and routing summary <=500 characters")
        require(len(body) <= 16000, "Record exceeds 16000 characters; split by semantic scope")
        require(len(encoded(meta)) <= 48000, "Record metadata is too large; split source scopes or records")
        for field in ("created_at", "updated_at"):
            stamp = dt.datetime.fromisoformat(meta[field])
            require(stamp.tzinfo is not None, "System timestamps require a timezone")
        for field in ("domains", "aliases"):
            values = meta.get(field, [])
            require(isinstance(values, list) and all(isinstance(x, str) and x for x in values),
                    "Expected string list: " + field)
        require(meta["domains"] and all(ID.fullmatch(d) for d in meta["domains"]),
                "At least one stable domain ID is required")
        unknown = [d for d in meta["domains"] if d not in DOMAINS]
        require(not unknown,
                "Unknown domain(s): " + ", ".join(unknown) +
                "; allowed domains: " + ", ".join(sorted(DOMAINS)))
        temporal_check(meta)
        if meta["basis"] == "inference":
            require(meta.get("uncertainty") and meta.get("evidence"),
                    "Inferences require uncertainty and evidence")
        if meta["type"] not in {"source"} and meta["basis"] != "navigation":
            require(meta.get("evidence"), "Factual records require evidence")
        records[rid] = {"meta": meta, "body": body, "path": path, "sha256": digest(value)}
    return records


def pages(items, size=80):
    return [items[i:i + size] for i in range(0, len(items), size)] or [[]]


def graph_model(files):
    """Validate authoritative files and build the shared graph model."""
    records = records_from(files)
    vault = json.loads(data_bytes(files["vault.json"]))
    require(vault.get("schema_version") == VERSION, "Unsupported vault schema version")
    nodes, edges = {}, []
    for rid, record in records.items():
        m = record["meta"]
        nodes[rid] = {k: m[k] for k in ("id", "type", "title", "summary", "domains",
                                       "temporal", "status", "basis")}
        nodes[rid].update(path=record["path"], sha256=record["sha256"],
                          aliases=m.get("aliases", []),
                          bytes=len(data_bytes(files[record["path"]])))
    for rid, record in records.items():
        m = record["meta"]
        for domain in m["domains"]:
            did = "domain:" + domain
            nodes.setdefault(did, {"id": did, "type": "domain", "title": domain})
            edges.append({"from": rid, "type": "in_domain", "to": did})
        for rel in m.get("relations", []):
            require(isinstance(rel, dict) and rel.get("type") in RELATIONS,
                    "Invalid relation on " + rid)
            target = rel.get("target")
            require(target in records, "Dangling relation: " + rid + " -> " + str(target))
            edge = {"from": rid, "type": rel["type"], "to": target}
            if rel.get("claim"):
                require(re.search(r"^##+ " + re.escape(rel["claim"]) + r"(?:[：: ]|$)",
                                  record["body"], re.M), "Missing relation claim heading")
                edge["claim"] = rel["claim"]
            edges.append(edge)
        for evidence in m.get("evidence", []):
            target = evidence.get("source")
            require(target in records and records[target]["meta"]["type"] == "source",
                    "Evidence needs an existing source node: " + str(target))
            require(isinstance(evidence.get("locator"), str) and evidence["locator"].strip(),
                    "Evidence requires a precise locator")
            if evidence.get("claim"):
                require(re.search(r"^##+ " + re.escape(evidence["claim"]) + r"(?:[：: ]|$)",
                                  record["body"], re.M), "Missing evidence claim heading")
            edges.append({"from": rid, "type": "derived_from", "to": target,
                          "locator": evidence["locator"],
                          **({"claim": evidence["claim"]} if evidence.get("claim") else {})})
        if m["type"] == "source":
            source = m.get("source", {})
            require(source.get("coverage") in {"unprocessed", "partial", "complete", "failed"},
                    "Source requires explicit extraction coverage")
            require(isinstance(source.get("coverage_note"), str) and source["coverage_note"],
                    "Source requires coverage_note")
            raw = source.get("raw_path")
            require(raw in files and raw.startswith("sources/"), "Source raw file is missing")
            require(source.get("sha256") == digest(files[raw]), "Source raw hash mismatch")
            units = source.get("units", [])
            require(isinstance(units, list), "Source units must be a list")
            unit_ids = set()
            for unit in units:
                require(isinstance(unit.get("id"), str) and ID.fullmatch(unit["id"])
                        and unit["id"] not in unit_ids, "Invalid or duplicate source unit ID")
                unit_ids.add(unit["id"])
                require(unit.get("status") in {"pending", "processed", "failed"} and unit.get("locator"),
                        "Source unit requires locator and processing status")
                targets = unit.get("record_ids", [])
                require(isinstance(targets, list) and all(t in records for t in targets),
                        "Source unit points to missing records")
                if unit["status"] == "processed":
                    require(targets or unit.get("note"), "Processed unit needs records or disposition note")
                for target in targets:
                    edges.append({"from": rid, "type": "extracted_as", "to": target,
                                  "locator": unit["locator"], "unit": unit["id"]})
            if source["coverage"] == "complete":
                require(units and source.get("scope") and all(u["status"] == "processed" for u in units),
                        "Complete extraction requires explicit scope and processed units")
    # Tombstones prevent repeated imports or stale operations from reviving a deleted ID.
    tombstones = set()
    for path, value in files.items():
        if path.startswith("operations/") and path.endswith(".json"):
            op = json.loads(data_bytes(value))
            tombstones.update(op.get("deleted_ids", []))
    require(not tombstones.intersection(records), "Deleted IDs cannot be reused")
    # Deduplicate identical edges, but retain independent locators.
    edges = sorted({json.dumps(e, sort_keys=True, ensure_ascii=False): e for e in edges}.values(),
                   key=lambda e: (e["from"], e["type"], e["to"], e.get("locator", "")))
    return vault, records, nodes, edges


def derive(files):
    """Generate a complete, deterministic graph; originals remain authoritative."""
    vault, records, nodes, edges = graph_model(files)
    adjacency = {rid: {"outgoing": [], "incoming": []} for rid in nodes}
    for edge in edges:
        adjacency[edge["from"]]["outgoing"].append(edge)
        adjacency[edge["to"]]["incoming"].append(edge)
    generated = {}
    for rid, node in sorted(nodes.items()):
        name = rid.replace(":", "--")
        adj_paths = {}
        for direction in ("outgoing", "incoming"):
            parts = pages(adjacency[rid][direction])
            refs = []
            for i, part in enumerate(parts):
                path = "graph/adjacency/{}/{}-{:05}.json".format(name, direction, i)
                generated[path] = encoded({"node": rid, "direction": direction, "edges": part})
                refs.append({"path": path, "count": len(part)})
            adj_paths[direction] = refs
        generated["graph/nodes/" + name + ".json"] = encoded({**node, "adjacency": adj_paths})
    for i, part in enumerate(pages(list(sorted(nodes)))):
        generated["graph/node-pages/{:05}.json".format(i)] = encoded(part)
    timed = sorted((r["meta"] for r in records.values()
                    if r["meta"]["temporal"]["mode"] != "none"), key=time_key, reverse=True)
    for i, part in enumerate(pages([m["id"] for m in timed])):
        generated["graph/timeline/{:05}.json".format(i)] = encoded(part)
    hashes = {p: digest(v) for p, v in sorted(files.items())}
    revision = digest(encoded(hashes))
    for i, part in enumerate(pages(list(hashes.items()))):
        generated["manifests/files/{:05}.json".format(i)] = encoded(dict(part))
    generated["graph/root.json"] = encoded({
        "schema_version": VERSION, "vault_id": vault["id"], "content_revision": revision,
        "record_count": len(records), "node_count": len(nodes), "edge_count": len(edges),
        "domains": sorted(n for n in nodes if n.startswith("domain:")),
        "node_pages": sorted(p for p in generated if p.startswith("graph/node-pages/")),
        "timeline_pages": sorted(p for p in generated if p.startswith("graph/timeline/")),
        "timeline_order": "semantic time descending; unknown last; none excluded",
        "incomplete_sources": sum(r["meta"].get("source", {}).get("coverage") in
                                  {"partial", "failed", "unprocessed"} for r in records.values())})
    return generated, records



def skill_bundle():
    bundle = {}
    candidates = []
    for name in ("SKILL.md", ".gitignore", "agents", "references", "scripts", "assets"):
        top = SKILL / name
        require(not top.is_symlink(), "Skill bundle cannot contain symlinks")
        if top.is_file():
            candidates.append(top)
        elif top.is_dir():
            candidates.extend(top.rglob("*"))
    for path in sorted(candidates):
        relative = path.relative_to(SKILL)
        if relative.parts[0] not in {"SKILL.md", ".gitignore", "agents", "references", "scripts", "assets"}:
            continue
        if any(p.startswith(".") or p == "__pycache__" for p in relative.parts) and relative.as_posix() != ".gitignore":
            continue
        if path.name == "config.json" or path.suffix in {".pyc", ".pyo"}:
            continue
        require(not path.is_symlink(), "Skill bundle cannot contain symlinks")
        if path.is_file():
            bundle[SKILL_PREFIX + relative.as_posix()] = path.read_bytes()
    require(SKILL_PREFIX + "SKILL.md" in bundle and
            SKILL_PREFIX + "scripts/atlas.py" in bundle, "Skill bundle is incomplete")
    return bundle


def checked(root):
    """Validate the existing graph against originals without regenerating files."""
    root = atlas_scope(root)
    require(all(owned(p) for p in root), "Unmanaged files inside atlas/; inspect before modifying")
    require("vault.json" in root, "Remote is not a Personal Atlas vault")
    require("graph/root.json" in root, "Remote graph missing; inspect and run reindex")
    files = canonical(root)
    vault, records, nodes, edges = graph_model(files)
    graph = json.loads(root["graph/root.json"])
    message = "Remote graph/index is stale or corrupt; inspect and run reindex"
    hashes = {p: digest(v) for p, v in sorted(files.items())}
    require(graph["content_revision"] == digest(encoded(hashes)), message)
    require(graph["schema_version"] == VERSION and graph["vault_id"] == vault["id"] and
            graph["record_count"] == len(records) and graph["node_count"] == len(nodes) and
            graph["edge_count"] == len(edges) and
            graph["domains"] == sorted(n for n in nodes if n.startswith("domain:")), message)
    expected = {"graph/root.json"}
    node_pages = []
    for i, part in enumerate(pages(list(hashes.items()))):
        path = "manifests/files/{:05}.json".format(i)
        expected.add(path)
        require(root.get(path) == encoded(dict(part)), message)
    for i, part in enumerate(pages(list(sorted(nodes)))):
        path = "graph/node-pages/{:05}.json".format(i)
        expected.add(path)
        node_pages.append(path)
        require(root.get(path) == encoded(part), message)
    require(graph["node_pages"] == node_pages, message)
    timed = sorted((r["meta"] for r in records.values()
                    if r["meta"]["temporal"]["mode"] != "none"), key=time_key, reverse=True)
    timeline = []
    for i, part in enumerate(pages([m["id"] for m in timed])):
        path = "graph/timeline/{:05}.json".format(i)
        expected.add(path)
        timeline.append(path)
        require(root.get(path) == encoded(part), message)
    require(graph["timeline_pages"] == timeline and graph["timeline_order"] ==
            "semantic time descending; unknown last; none excluded", message)
    require(graph["incomplete_sources"] == sum(r["meta"].get("source", {}).get("coverage") in
            {"partial", "failed", "unprocessed"} for r in records.values()), message)
    adjacency = {rid: {"outgoing": [], "incoming": []} for rid in nodes}
    for edge in edges:
        adjacency[edge["from"]]["outgoing"].append(edge)
        adjacency[edge["to"]]["incoming"].append(edge)
    for rid, node in nodes.items():
        path = "graph/nodes/" + rid.replace(":", "--") + ".json"
        expected.add(path)
        stored = json.loads(root[path])
        require({k: v for k, v in stored.items() if k != "adjacency"} == node, message)
        for direction in ("outgoing", "incoming"):
            refs = []
            for i, part in enumerate(pages(adjacency[rid][direction])):
                path = "graph/adjacency/{}/{}-{:05}.json".format(rid.replace(":", "--"), direction, i)
                expected.add(path)
                refs.append({"path": path, "count": len(part)})
                require(json.loads(root[path]) == {"node": rid, "direction": direction, "edges": part}, message)
            require(stored["adjacency"][direction] == refs, message)
    require({p for p in root if p.startswith(GENERATED)} == expected, message)
    return root, records


class Snapshot(dict):
    """Files supplied by the agent's existing tools, never fetched by this module."""
    def __init__(self, files, commit, urls=None, *, _atlas_relative=False):
        require(commit is None or isinstance(commit, str) and
                re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit), "Invalid commit")
        require(isinstance(files, dict), "Files must be a complete path/content mapping")
        for path in files:
            require(isinstance(path, str) and path and not path.startswith("/") and
                    "\\" not in path and all(part not in {"", ".", ".."} for part in path.split("/")),
                    "Unsafe repository path")
        super().__init__(files)
        self.commit, self.urls = commit, urls or {}
        self._atlas_relative = _atlas_relative

    def url(self, path):
        return self.urls.get(path)



def atlas_scope(root):
    """View only atlas/ at a repository commit; graph paths stay vault-relative.

    Inputs and publication paths are repository-relative. Unrelated repository
    files never enter Atlas validation, rendering, or deletion calculations.
    """
    if isinstance(root, Snapshot) and root._atlas_relative:
        return root
    require("atlas" not in root, "atlas is a file, not a directory; choose a repository without this conflict")
    files = {p[len(ATLAS_PREFIX):]: v for p, v in root.items() if p.startswith(ATLAS_PREFIX)}
    urls = {p[len(ATLAS_PREFIX):]: root.url(p) for p in root if p.startswith(ATLAS_PREFIX)}
    return Snapshot(files, root.commit, urls, _atlas_relative=True)


def publish(root, proposed, message):
    root = atlas_scope(root)
    Snapshot(proposed, root.commit)  # Validate every output path before prefixing.
    require(all(owned(p) for p in proposed), "Unexpected file in operation")
    return {"status": "prepared", "base_commit": root.commit, "message": message,
            "base_path": ATLAS_PREFIX,
            "files_base64": {ATLAS_PREFIX + p: base64.b64encode(v).decode("ascii")
                             for p, v in proposed.items() if p not in root or root[p] != v},
            "delete_paths": sorted(ATLAS_PREFIX + p for p in set(root) - set(proposed)), "remote_confirmed": False}


def initialize(root):
    root = atlas_scope(root)
    if root:
        checked(root)
        return {"status": "already_initialized", "commit": root.commit,
                "verification": "supplied_files", "remote_confirmed": False}
    vault = {"schema_version": VERSION, "id": uid("vault"), "created_at": now(),
             "phase": "ready", "storage": "git"}
    proposed = {"vault.json": encoded(vault), ".gitignore": b".DS_Store\n.local/\n__pycache__/\n*.pyc\n"}
    generated, _ = derive(proposed)
    return {**publish(root, {**proposed, **generated}, "Initialize Personal Atlas"),
            "vault_id": vault["id"]}


class LazyRecords(Mapping):
    def __init__(self, root, metadata_only=False):
        self.root, self.metadata_only = root, metadata_only
        graph = json.loads(root["graph/root.json"])
        self.ids = []
        for path in graph["node_pages"]:
            self.ids.extend(r for r in json.loads(root[path]) if ":" not in r)
        require(len(self.ids) == len(set(self.ids)), "Duplicate graph node IDs")
        self.id_set = set(self.ids)

    def __iter__(self):
        return iter(self.ids)

    def __len__(self):
        return len(self.ids)

    def __contains__(self, key):
        return key in self.id_set

    def __getitem__(self, key):
        if key not in self.id_set:
            raise KeyError(key)
        node = json.loads(self.root["graph/nodes/" + key + ".json"])
        if self.metadata_only:
            return {"meta": node, "body": "", "path": node["path"], "sha256": node["sha256"]}
        value = self.root[node["path"]]
        require(digest(value) == node["sha256"], "Record differs from graph; run verify")
        meta, body = parse_markdown(value)
        return {"meta": meta, "body": body, "path": node["path"], "sha256": node["sha256"]}


def new_meta(meta, old=None):
    m = dict(meta)
    m.setdefault("aliases", [])
    m.setdefault("relations", [])
    m.setdefault("evidence", [])
    m.setdefault("status", "active")
    m["created_at"] = old["meta"]["created_at"] if old else now()
    m["updated_at"] = now()
    return m


def apply_plan(root, plan):
    root = atlas_scope(root)
    files, records = checked(root)
    require(isinstance(plan.get("operation_id"), str) and ID.fullmatch(plan["operation_id"]),
            "Plan needs a stable operation_id")
    op_path = "operations/" + plan["operation_id"] + ".json"
    plan_hash = digest(encoded(plan))
    if op_path in files:
        prior = json.loads(data_bytes(files[op_path]))
        require(prior.get("plan_sha256") == plan_hash, "Operation ID reused with different content")
        return {"status": "already_applied", "commit": root.commit, "remote_confirmed": False}
    require(plan.get("base_commit") == root.commit, "Stale base_commit; inspect changes and rebuild plan")
    require(isinstance(plan.get("changes"), list) and plan["changes"], "Empty change plan")
    proposed = canonical(files)
    touched, deleted, deleted_hashes = set(), [], []
    for change in plan["changes"]:
        action = change.get("action")
        if action == "put":
            meta = change["meta"]
            rid = meta["id"]
            require(rid not in touched, "An operation can change each ID once")
            touched.add(rid)
            old = records.get(rid)
            if old:
                require(change.get("expected_sha256") == old["sha256"], "Record hash changed: " + rid)
                proposed.pop(old["path"])
            else:
                require(change.get("expected_sha256") is None, "Record does not exist: " + rid)
            require(isinstance(change.get("body"), str) and change["body"].strip(), "Body required")
            meta = new_meta(meta, old)
            proposed[record_path(meta)] = markdown(meta, change["body"])
        elif action == "delete":
            rid = change["id"]
            require(rid not in touched and rid in records, "Unknown or repeated delete ID")
            touched.add(rid)
            old = records[rid]
            require(change.get("expected_sha256") == old["sha256"], "Record hash changed: " + rid)
            proposed.pop(old["path"])
            deleted.append(rid)
            if old["meta"]["type"] == "source":
                proposed.pop(old["meta"]["source"]["raw_path"], None)
                deleted_hashes.append(old["meta"]["source"]["sha256"])
        else:
            raise AtlasError("Unknown change action: " + str(action))
    proposed[op_path] = encoded({"id": plan["operation_id"], "plan_sha256": plan_hash,
                                  "recorded_at": now(), "base_commit": root.commit,
                                  "changed_ids": sorted(touched), "deleted_ids": deleted,
                                  "deleted_source_hashes": deleted_hashes})
    derived, _ = derive(proposed)  # A dangling edge/source blocks the entire operation.
    result = publish(root, {**proposed, **derived}, "Atlas operation " + plan["operation_id"])
    return {**result, "operation_id": plan["operation_id"], "changed_ids": sorted(touched),
            "deleted_ids": deleted, "history_purged": False}



def page_result(items, args):
    require(args.limit > 0 and args.limit <= 200 and args.offset >= 0, "Invalid pagination")
    end = args.offset + args.limit
    return {"total": len(items), "offset": args.offset, "items": items[args.offset:end],
            "next_offset": end if end < len(items) else None, "truncated": end < len(items)}


def summary(record):
    m = record["meta"]
    return {**{k: m[k] for k in ("id", "type", "title", "summary", "temporal", "status", "basis")},
            "path": record["path"], "sha256": record["sha256"]}


def selected(records, args):
    result = []
    for record in records.values():
        m = record["meta"]
        if getattr(args, "kind", None) and m["type"] != args.kind:
            continue
        if getattr(args, "domain", None) and args.domain not in m["domains"]:
            continue
        if getattr(args, "status", None) and m["status"] != args.status:
            continue
        if not getattr(args, "include_retracted", False) and m["status"] == "retracted":
            continue
        result.append(record)
    return result


def query(root, records, args):
    candidates = selected(records, args)
    if args.command == "timeline":
        candidates = [r for r in candidates if r["meta"]["temporal"]["mode"] != "none"]
        for field in ("since", "until"):
            date_value(getattr(args, field))
        def included(r):
            value = record_date(r["meta"])
            if not value:
                return not (args.since or args.until)
            # Select the start/point calendar bucket, not interval overlap.
            lower, upper = date_bounds(value)
            return (not args.since or upper >= date_bounds(args.since)[0]) and (
                    not args.until or lower <= date_bounds(args.until)[1])
        candidates = sorted(filter(included, candidates), key=lambda r: time_key(r["meta"]), reverse=True)
        items = [summary(r) for r in candidates]
    elif args.command == "list":
        items = [summary(r) for r in sorted(candidates, key=lambda r: r["meta"]["id"])]
    else:
        terms = [t.casefold() for t in re.split(r"\s+", args.query.strip()) if t]
        require(terms, "Search query is empty")
        hits = []
        for r in candidates:
            m = r["meta"]
            names = [m["id"], m["title"], *m.get("aliases", [])]
            name_text = " ".join(names).casefold()
            synopsis = m["summary"].casefold()
            body = r["body"].casefold()
            score = sum((8 if t in name_text else 0) + (3 if t in synopsis else 0) +
                        (1 if t in body else 0) for t in terms)
            matches = [t for t in terms if t in name_text or t in synopsis or t in body]
            if score and (args.match == "any" or len(matches) == len(terms)):
                hits.append({**summary(r), "score": score, "matched_terms": matches})
        items = sorted(hits, key=lambda r: (-r["score"], r["id"]))
    return {"snapshot": root.commit, "selection": args.command,
            "scope": {"type": getattr(args, "kind", None), "domain": args.domain,
                      "status": args.status, "retracted_included": args.include_retracted},
            **page_result(items, args)}


def neighbors(root, records, args):
    name = args.id.replace(":", "--")
    require(ID.fullmatch(args.id) or re.fullmatch(r"domain:[a-z][a-z0-9_-]{0,95}", args.id),
            "Invalid graph node ID")
    node_path = "graph/nodes/" + name + ".json"
    require(node_path in root, "Graph node does not exist")
    node = json.loads(root[node_path])
    edges = []
    directions = ["incoming", "outgoing"] if args.direction == "both" else [args.direction]
    for direction in directions:
        for ref in node["adjacency"][direction]:
            for edge in json.loads(root[ref["path"]])["edges"]:
                if args.relation and edge["type"] != args.relation:
                    continue
                other = edge["to"] if direction == "outgoing" else edge["from"]
                record = records.get(other)
                if record and record["meta"]["status"] == "retracted" and not args.include_retracted:
                    continue
                edges.append({"direction": direction, **edge,
                              "neighbor": summary(record) if record else {"id": other}})
    return {"snapshot": root.commit, "node": {k: v for k, v in node.items() if k != "adjacency"},
            **page_result(edges, args)}



def ingest(root, args, content):
    root = atlas_scope(root)
    files, records = checked(root)
    require(content and len(content) <= MAX_SOURCE_BYTES, "Source must be 1 byte to 20 MiB; split larger input in memory")
    require(args.name and Path(args.name).name == args.name and args.name not in {'.', '..'},
            "Source name must be a filename, not a path")
    content_hash = digest(content)
    for record in records.values():
        if record["meta"].get("source", {}).get("sha256") == content_hash:
            return {"status": "already_imported", "source_id": record["meta"]["id"],
                    "commit": root.commit, "remote_confirmed": False}
    for path in files:
        if path.startswith("operations/"):
            operation = json.loads(files[path])
            require(content_hash not in operation.get("deleted_source_hashes", []),
                    "This source was deleted; explicit restoration under a new policy is required")
    rid = uid("src")
    suffix = Path(args.name).suffix.lower()
    require(not suffix or re.fullmatch(r"\.[a-z0-9_-]{1,20}", suffix), "Unsupported filename suffix")
    raw_path = "sources/" + rid + "/original" + suffix
    meta = new_meta({"id": rid, "type": "source", "title": args.title or args.name,
                     "summary": args.summary or "原始来源；尚未完成信息提取。",
                     "domains": args.domain or ["life"], "temporal": {"mode": "none"},
                     "basis": "document", "source": {"raw_path": raw_path,
                         "sha256": content_hash, "original_name": args.name,
                         "coverage": "unprocessed", "coverage_note": "尚未提取；原文件完整保留。"}})
    proposed = canonical(files)
    proposed[raw_path] = content
    proposed[record_path(meta)] = markdown(meta, "# " + meta["title"] +
        "\n\n原文作为数据保存。使用行号、消息 ID、页码或时间码定位提取依据。\n")
    op = uid("op")
    proposed["operations/" + op + ".json"] = encoded({"id": op, "recorded_at": now(),
        "changed_ids": [rid], "deleted_ids": [], "source_sha256": content_hash})
    derived, _ = derive(proposed)
    result = publish(root, {**proposed, **derived}, "Import source " + rid)
    return {**result, "source_id": rid, "sha256": content_hash, "extraction": "unprocessed"}


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--snapshot", help="Require the supplied snapshot to match")
    sub = p.add_subparsers(dest="command", required=True)
    p.add_argument("--stdin", action="store_true", required=True, help="Snapshot envelope from agent tools")
    for command in ("init", "status", "verify", "reindex", "graph", "archive-skill"):
        sub.add_parser(command)
    apply = sub.add_parser("apply")
    visual = sub.add_parser("visualize", help="Export a complete standalone HTML graph")
    visual.add_argument("--output", required=True, help="New HTML deliverable path")
    ing = sub.add_parser("ingest")
    ing.add_argument("--name", required=True)
    ing.add_argument("--title")
    ing.add_argument("--summary")
    ing.add_argument("--domain", action="append")
    for command in ("read", "source"):
        q = sub.add_parser(command)
        q.add_argument("id")
        q.add_argument("--offset", type=int, default=0)
        q.add_argument("--limit", type=int, default=80)
    for command in ("search", "list", "timeline", "neighbors"):
        q = sub.add_parser(command)
        q.add_argument("--offset", type=int, default=0)
        q.add_argument("--limit", type=int, default=30)
        q.add_argument("--include-retracted", action="store_true")
        if command == "neighbors":
            q.add_argument("id")
            q.add_argument("--direction", choices=["incoming", "outgoing", "both"], default="both")
            q.add_argument("--relation")
        else:
            q.add_argument("--type", dest="kind", choices=sorted(KINDS))
            q.add_argument("--domain")
            q.add_argument("--status", choices=sorted(STATUSES))
        if command == "search":
            q.add_argument("query")
            q.add_argument("--match", choices=["all", "any"], default="all")
        if command == "timeline":
            q.add_argument("--since")
            q.add_argument("--until")
    return p


def _run(args, payload=None):
    payload = payload if payload is not None else json.load(sys.stdin)
    root = Snapshot({p: base64.b64decode(v, validate=True)
                     for p, v in payload["files_base64"].items()},
                    payload.get("snapshot"), payload.get("urls"))
    require(not args.snapshot or args.snapshot == root.commit, "Snapshot mismatch")
    root = atlas_scope(root)
    if args.command == "init":
        return initialize(root)
    require(root.commit is not None, "Existing Atlas needs a fixed commit")
    require("vault.json" in root, "atlas/vault.json missing; initialize atlas/ explicitly (root-level vaults are not loaded)")
    vault = json.loads(root["vault.json"])
    require(vault.get("schema_version") == VERSION and vault.get("phase") != "initializing",
            "Remote initialization incomplete or incompatible; run init")
    if args.command == "visualize":
        from visualization import collect, export
        return export(collect(root), args.output)
    if args.command == "reindex":
        require(all(owned(p) for p in root), "Unmanaged remote files; inspect before reindex")
        files = canonical(root)
        derived, _ = derive(files)
        return publish(root, {**files, **derived}, "Rebuild remote Atlas graph")
    if args.command == "archive-skill":
        checked(root)
        files = {p: v for p, v in canonical(root).items() if not p.startswith(SKILL_PREFIX)}
        files.update(skill_bundle())
        derived, _ = derive(files)
        return publish(root, {**files, **derived}, "Update complete Atlas skill snapshot")
    if args.command == "apply":
        return apply_plan(root, payload["plan"])
    if args.command == "ingest":
        return ingest(root, args, base64.b64decode(payload["source_base64"], validate=True))
    require("graph/root.json" in root, "Remote graph missing; inspect and run reindex")
    graph = json.loads(root["graph/root.json"])
    require(graph["vault_id"] == vault["id"], "Remote graph/vault identity mismatch")
    if args.command in {"status", "verify", "graph"}:
        if args.command == "verify":
            checked(root)
        return {"status": "valid" if args.command == "verify" else "snapshot_available",
                "snapshot": root.commit, "graph": graph, "remote_checked": False,
                "verification": "full" if args.command == "verify" else "entrypoint_only",
                "storage": "agent_supplied_snapshot", "local_data_storage": False}
    records = LazyRecords(root, metadata_only=args.command in {"list", "timeline", "neighbors"})
    if args.command in {"search", "list", "timeline"}:
        return query(root, records, args)
    if args.command == "neighbors":
        return neighbors(root, records, args)
    require(args.id in records, "Record not found")
    record = records[args.id]
    if args.command == "read":
        return {"snapshot": root.commit, "meta": record["meta"], "sha256": record["sha256"],
                "url": root.url(record["path"]), "body": page_result(record["body"].splitlines(), args)}
    require(record["meta"]["type"] == "source", "Not a source record")
    raw = record["meta"]["source"]["raw_path"]
    content = root[raw]
    require(digest(content) == record["meta"]["source"]["sha256"], "Source hash mismatch")
    try:
        lines = content.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return {"snapshot": root.commit, "source_id": args.id, "binary": True,
                "path": raw, "url": root.url(raw), "sha256": digest(content),
                "local_file": None, "bytes": len(content)}
    result = page_result(lines, args)
    require(all(len(line) <= 16000 for line in result["items"]),
            "Long source line; use an in-memory format parser with stable locators")
    return {"snapshot": root.commit, "source_id": args.id, "total_lines": result["total"],
            "url": root.url(raw),
            "lines": [{"line": args.offset + i + 1, "text": line} for i, line in enumerate(result["items"])],
            "next_offset": result["next_offset"], "truncated": result["truncated"]}



def run(args, payload=None):
    return {**_run(args, payload), "base_path": ATLAS_PREFIX}


def main():
    args = parser().parse_args()
    try:
        result = run(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (AtlasError, ValueError, KeyError, TypeError, OSError) as error:
        print(json.dumps({"status": "error", "error": str(error),
                          "remote_confirmed": False}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())