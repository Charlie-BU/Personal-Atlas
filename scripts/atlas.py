#!/usr/bin/env python3
"""Personal Atlas: Markdown records, a derived JSON graph, transactional Git storage.

Python 3.9+, Git, standard library only. All public output is JSON.
"""
import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import uuid
from collections.abc import Mapping

VERSION = 1
SKILL = Path(__file__).resolve().parents[1]
ID = re.compile(r"^[a-z][a-z0-9_-]{0,95}$")
KINDS = {"person", "organization", "place", "profile", "topic", "preference",
         "routine", "state", "event", "decision", "goal", "project", "asset", "source"}
STATUSES = {"active", "historical", "disputed", "retracted"}
RELATIONS = {"about", "part_of", "participated_in", "located_at", "works_at",
             "knows", "supports", "contradicts", "supersedes", "derived_from",
             "depends_on", "related_to"}
CANONICAL = ("records/", "sources/", "operations/")
GENERATED = ("graph/", "manifests/")


class AtlasError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise AtlasError(message)


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def uid(prefix):
    return prefix + "_" + uuid.uuid4().hex


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def data_bytes(value):
    return value.read_bytes() if isinstance(value, Path) else value


def digest(value):
    h = hashlib.sha256()
    if isinstance(value, Path):
        with value.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                h.update(chunk)
    else:
        h.update(value)
    return h.hexdigest()


def git(root, *args, input=None, binary=False, check=True):
    cmd = ["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false",
           "-c", "user.name=Personal Atlas", "-c", "user.email=atlas@localhost",
           "-C", str(root), *args]
    p = subprocess.run(cmd, input=input, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and p.returncode:
        raise AtlasError("Git failed: " + p.stderr.decode(errors="replace").strip())
    return p.stdout if binary else p.stdout.decode().strip()


def head(root):
    return git(root, "rev-parse", "--verify", "HEAD", check=False) or None


def safe_relative(path):
    p = Path(path)
    require(not p.is_absolute() and ".." not in p.parts and str(p) == path,
            "Unsafe relative path: " + path)
    require(path and not path.startswith(".git/"), "Reserved path")
    return p


def owned(path):
    return path in {"vault.json", ".gitignore"} or path.startswith(CANONICAL + GENERATED)


def atomic_file(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".atlas-" + uuid.uuid4().hex)
    try:
        with temporary.open("wb") as out:
            if isinstance(value, Path):
                with value.open("rb") as src:
                    shutil.copyfileobj(src, out)
            else:
                out.write(value)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def inventory(root):
    result = {}
    for folder, directories, filenames in os.walk(root):
        if Path(folder) == root:
            directories[:] = [d for d in directories if d != ".git"]
        for name in directories + filenames:
            path = Path(folder) / name
            rel = path.relative_to(root).as_posix()
            require(not path.is_symlink(), "Symlink inside vault is not supported: " + rel)
            if path.is_file():
                require(owned(rel), "Unmanaged file in dedicated vault: " + rel)
                result[rel] = path
    return result


def canonical(files):
    return {p: v for p, v in files.items() if not p.startswith(GENERATED)}


@contextlib.contextmanager
def locked(root, recovery=False):
    require((root / ".git").is_dir(), "Expected a dedicated Git checkout")
    with (root / ".git/atlas.lock").open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise AtlasError("Vault is busy; retry after the current operation finishes")
        require(recovery or not (root / ".git/atlas-transaction").exists(),
                "Interrupted transaction detected; run recover before reading or writing")
        yield


def clean(root):
    require(not git(root, "status", "--porcelain", "--untracked-files=all"),
            "Uncommitted or external edits detected; inspect them and run reindex")


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


def derive(files):
    """Generate a complete, deterministic graph; originals remain authoritative."""
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


def checked(root):
    files = inventory(root)
    require("vault.json" in files, "No Personal Atlas vault at this location")
    generated, records = derive(canonical(files))
    existing = {p: v for p, v in files.items() if p.startswith(GENERATED)}
    require(existing.keys() == generated.keys() and
            all(digest(existing[p]) == digest(v) for p, v in generated.items()),
            "Graph/index is stale or corrupt; inspect external edits and run reindex")
    return files, records


class LazyRecords(Mapping):
    """Read only requested records; full-text search still deliberately scans records."""
    def __init__(self, root):
        self.root = root
        graph = json.loads((root / "graph/root.json").read_text())
        self.ids = []
        for path in graph["node_pages"]:
            self.ids.extend(r for r in json.loads((root / path).read_text()) if ":" not in r)
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
        node = json.loads((self.root / "graph/nodes" / (key + ".json")).read_text())
        meta, body = parse_markdown(self.root / node["path"])
        return {"meta": meta, "body": body, "path": node["path"], "sha256": node["sha256"]}


def read_snapshot(root, full=False):
    clean(root)
    stamp = root / ".git/atlas-verified.json"
    current = head(root)
    cached = json.loads(stamp.read_text()) if stamp.exists() else {}
    if full or cached.get("commit") != current or cached.get("schema_version") != VERSION:
        checked(root)
        atomic_file(stamp, encoded({"commit": current, "schema_version": VERSION}))
    return LazyRecords(root)


def finish_write(root, config, result):
    if config["sync"] == "after_write" and config.get("remote"):
        try:
            result["sync"] = synchronize(root, config)
        except AtlasError as e:
            result["sync"] = {"status": "pending_or_conflicted", "error": str(e),
                              "remote_confirmed": False}
    else:
        result["sync"] = {"status": "not_requested", "remote_confirmed": False}
    return result


def recover(root):
    folder = root / ".git/atlas-transaction"
    if not folder.exists():
        return {"status": "no_interrupted_transaction"}
    journal = json.loads((folder / "journal.json").read_text())
    if journal.get("target") and head(root) == journal["target"]:
        shutil.rmtree(folder)
        return {"status": "local_saved", "commit": head(root)}
    require(head(root) == journal["base"], "HEAD changed independently; manual recovery required")
    for path, existed in journal["before"].items():
        destination = root / safe_relative(path)
        if existed:
            atomic_file(destination, folder / "before" / path)
        elif destination.exists():
            destination.unlink()
    index = root / ".git/index"
    if (folder / "index").exists():
        atomic_file(index, folder / "index")
    elif index.exists():
        index.unlink()
    shutil.rmtree(folder)
    return {"status": "rolled_back", "commit": head(root)}


def publish(root, proposed, message, parents=None, target=None):
    """Caller holds lock. Journal precedes writes; Git ref is the commit boundary."""
    existing = inventory(root)
    changed = sorted(p for p in existing.keys() | proposed.keys()
                     if p not in existing or p not in proposed or digest(existing[p]) != digest(proposed[p]))
    if not changed and target is None:
        return {"status": "unchanged", "commit": head(root)}
    base = head(root)
    branch = git(root, "symbolic-ref", "HEAD")
    journal = {"base": base, "target": None, "before": {p: p in existing for p in changed}}
    folder = root / ".git/atlas-transaction"
    prepare = root / (".git/atlas-preparing-" + uuid.uuid4().hex)
    prepare.mkdir()
    try:
        for path in changed:
            if path in existing:
                atomic_file(prepare / "before" / path, existing[path])
        if (root / ".git/index").exists():
            shutil.copyfile(root / ".git/index", prepare / "index")
        atomic_file(prepare / "journal.json", encoded(journal))
        os.replace(prepare, folder)
        # Resolve references to files that will be replaced/deleted before writing them.
        for path in changed:
            if path in proposed:
                value = proposed[path]
                if isinstance(value, Path) and value.is_relative_to(root):
                    rel = value.relative_to(root).as_posix()
                    if rel in journal["before"] and journal["before"][rel]:
                        value = folder / "before" / rel
                atomic_file(root / path, value)
            else:
                (root / path).unlink()
        if os.environ.get("ATLAS_TEST_INTERRUPT") == "after_files":
            os._exit(86)
        git(root, "add", "--all", "--", ".")
        if target is None:
            tree = git(root, "write-tree")
            parent_args = []
            for parent in (parents if parents is not None else ([base] if base else [])):
                parent_args += ["-p", parent]
            target = git(root, "commit-tree", tree, *parent_args, input=(message + "\n").encode())
        else:
            require(git(root, "write-tree") == git(root, "rev-parse", target + "^{tree}"),
                    "Fast-forward tree does not match validated remote tree")
        journal["target"] = target
        atomic_file(folder / "journal.json", encoded(journal))
        git(root, "update-ref", branch, target, base or "0" * 40)
        if os.environ.get("ATLAS_TEST_INTERRUPT") == "after_commit":
            os._exit(87)
        shutil.rmtree(folder)
        atomic_file(root / ".git/atlas-verified.json", encoded({"commit": target, "schema_version": VERSION}))
        return {"status": "local_saved", "commit": target, "changed_files": len(changed)}
    except Exception:
        if folder.exists():
            recover(root)
        if prepare.exists():
            shutil.rmtree(prepare)
        raise


def config_path(args):
    return Path(args.config).expanduser().resolve() if args.config else SKILL / ".local/config.json"


def load_config(args):
    path = config_path(args)
    require(path.is_file(), "Not configured; run init with a local directory or Git remote")
    config = json.loads(path.read_text())
    root = Path(config["checkout"]).expanduser()
    if not root.is_absolute():
        root = (path.parent / root).resolve()
    require(config.get("schema_version") == VERSION, "Unsupported configuration version")
    require((root / "vault.json").is_file(), "Configured checkout is unavailable")
    vault = json.loads((root / "vault.json").read_text())
    require(vault["id"] == config["vault_id"], "Configured vault identity does not match checkout")
    return config, root


def initialize(args):
    config_file = config_path(args)
    require(not args.branch.startswith("-") and
            git(SKILL, "check-ref-format", "--branch", args.branch, check=False) == args.branch,
            "Invalid branch name")
    require(not config_file.exists(), "Configuration already exists; it will not be overwritten")
    root = Path(args.path).expanduser().resolve() if args.path else config_file.parent / "store"
    require(not root.exists() or not any(root.iterdir()), "Initialization needs a new empty directory")
    if args.remote:
        require(not args.remote.startswith("-"), "Invalid remote")
        root.parent.mkdir(parents=True, exist_ok=True)
        p = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "clone", "--no-checkout",
                            "--", args.remote, str(root)], capture_output=True)
        require(p.returncode == 0, "Clone failed: " + p.stderr.decode(errors="replace"))
        remote_ref = "refs/remotes/origin/" + args.branch
        remote_head = git(root, "rev-parse", "--verify", remote_ref, check=False)
        if remote_head:
            git(root, "checkout", "-B", args.branch, remote_ref)
        else:
            require(not git(root, "for-each-ref", "--format=%(refname)", "refs/remotes"),
                    "Remote is not empty and requested branch does not exist")
            git(root, "symbolic-ref", "HEAD", "refs/heads/" + args.branch)
    else:
        root.mkdir(parents=True, exist_ok=True)
        git(root, "init", "-b", args.branch)
    with locked(root):
        if (root / "vault.json").exists():
            checked(root)
        else:
            require(not inventory(root), "Remote is not an empty Atlas vault; refusing initialization")
            vault = {"schema_version": VERSION, "id": uid("vault"), "created_at": now()}
            files = {"vault.json": encoded(vault), ".gitignore": b".DS_Store\n"}
            derived, _ = derive(files)
            publish(root, {**files, **derived}, "Initialize Personal Atlas")
        vault = json.loads((root / "vault.json").read_text())
        config = {"schema_version": VERSION, "vault_id": vault["id"], "checkout": str(root),
                  "remote": args.remote, "branch": args.branch, "sync": args.sync}
        atomic_file(config_file, encoded(config))
    return {"status": "configured", "config": str(config_file), "checkout": str(root),
            "vault_id": vault["id"], "remote_confirmed": False}


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
    files, records = checked(root)
    clean(root)
    require(isinstance(plan.get("operation_id"), str) and ID.fullmatch(plan["operation_id"]),
            "Plan needs a stable operation_id")
    op_path = "operations/" + plan["operation_id"] + ".json"
    plan_hash = digest(encoded(plan))
    if op_path in files:
        prior = json.loads(data_bytes(files[op_path]))
        require(prior.get("plan_sha256") == plan_hash, "Operation ID reused with different content")
        return {"status": "already_applied", "commit": head(root)}
    require(plan.get("base_commit") == head(root), "Stale base_commit; inspect changes and rebuild plan")
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
                                  "recorded_at": now(), "base_commit": head(root),
                                  "changed_ids": sorted(touched), "deleted_ids": deleted,
                                  "deleted_source_hashes": deleted_hashes})
    derived, _ = derive(proposed)  # A dangling edge/source blocks the entire operation.
    result = publish(root, {**proposed, **derived}, "Atlas operation " + plan["operation_id"])
    return {**result, "operation_id": plan["operation_id"], "changed_ids": sorted(touched),
            "deleted_ids": deleted, "history_purged": False}


def ingest(root, args):
    files, records = checked(root)
    clean(root)
    original = Path(args.file).expanduser().resolve()
    require(original.is_file(), "Source file not found")
    content_hash = digest(original)
    for record in records.values():
        if record["meta"].get("source", {}).get("sha256") == content_hash:
            return {"status": "already_imported", "source_id": record["meta"]["id"],
                    "commit": head(root)}
    # Prevent forgotten originals from silently coming back through a later import.
    for path, value in files.items():
        if path.startswith("operations/"):
            operation = json.loads(data_bytes(value))
            require(content_hash not in operation.get("deleted_source_hashes", []),
                    "This source was deleted; explicit restoration under a new policy is required")
    rid = uid("src")
    raw_path = "sources/" + rid + "/original" + original.suffix.lower()
    meta = new_meta({"id": rid, "type": "source", "title": args.title or original.name,
                     "summary": args.summary or "原始来源；尚未完成信息提取。",
                     "domains": args.domain or ["inbox"], "temporal": {"mode": "none"},
                     "basis": "document", "source": {"raw_path": raw_path,
                         "sha256": content_hash, "original_name": original.name,
                         "coverage": "unprocessed", "coverage_note": "尚未提取；原文件完整保留。"}})
    proposed = canonical(files)
    proposed[raw_path] = original
    proposed[record_path(meta)] = markdown(meta, "# " + meta["title"] +
        "\n\n原文作为数据保存。使用行号、消息 ID、页码或时间码定位提取依据。\n")
    op = uid("op")
    proposed["operations/" + op + ".json"] = encoded({"id": op, "recorded_at": now(),
        "changed_ids": [rid], "deleted_ids": [], "source_sha256": content_hash})
    derived, _ = derive(proposed)
    result = publish(root, {**proposed, **derived}, "Import source " + rid)
    return {**result, "source_id": rid, "sha256": content_hash, "extraction": "unprocessed"}


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
    return {"snapshot": head(root), "selection": args.command,
            "scope": {"type": getattr(args, "kind", None), "domain": args.domain,
                      "status": args.status, "retracted_included": args.include_retracted},
            **page_result(items, args)}


def neighbors(root, records, args):
    name = args.id.replace(":", "--")
    require(ID.fullmatch(args.id) or re.fullmatch(r"domain:[a-z][a-z0-9_-]{0,95}", args.id),
            "Invalid graph node ID")
    node_path = root / "graph/nodes" / (name + ".json")
    require(node_path.is_file(), "Graph node does not exist")
    node = json.loads(node_path.read_text())
    edges = []
    directions = ["incoming", "outgoing"] if args.direction == "both" else [args.direction]
    for direction in directions:
        for ref in node["adjacency"][direction]:
            for edge in json.loads((root / ref["path"]).read_text())["edges"]:
                if args.relation and edge["type"] != args.relation:
                    continue
                other = edge["to"] if direction == "outgoing" else edge["from"]
                record = records.get(other)
                if record and record["meta"]["status"] == "retracted" and not args.include_retracted:
                    continue
                edges.append({"direction": direction, **edge,
                              "neighbor": summary(record) if record else {"id": other}})
    return {"snapshot": head(root), "node": {k: v for k, v in node.items() if k != "adjacency"},
            **page_result(edges, args)}


@contextlib.contextmanager
def revision_files(root, revision):
    with tempfile.TemporaryDirectory(prefix="atlas-revision-") as directory:
        destination = Path(directory)
        # Stream archives, including large original files, without buffering the corpus.
        process = subprocess.Popen(["git", "-C", str(root), "archive", revision], stdout=subprocess.PIPE)
        try:
            with tarfile.open(fileobj=process.stdout, mode="r|") as archive:
                for item in archive:
                    relative = item.name.rstrip("/")
                    safe_relative(relative)
                    require(item.isdir() or item.isfile(), "Unsupported remote archive entry")
                    if item.isfile():
                        require(owned(relative), "Unmanaged remote file: " + relative)
                        path = destination / relative
                        path.parent.mkdir(parents=True, exist_ok=True)
                        with archive.extractfile(item) as source, path.open("wb") as out:
                            shutil.copyfileobj(source, out)
            require(process.wait() == 0, "Could not read remote revision")
            yield inventory(destination)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()


def synchronize(root, config):
    require(config.get("remote"), "No remote configured")
    require(git(root, "remote", "get-url", "origin") == config["remote"], "Remote URL changed")
    require(git(root, "symbolic-ref", "--short", "HEAD") == config["branch"], "Branch changed")
    checked(root)
    clean(root)
    git(root, "fetch", "origin")
    remote_ref = "refs/remotes/origin/" + config["branch"]
    remote_head = git(root, "rev-parse", "--verify", remote_ref, check=False) or None
    local_head = head(root)
    if remote_head and remote_head != local_head:
        base = git(root, "merge-base", local_head, remote_head, check=False)
        require(base, "Unrelated repository histories; refusing synchronization")
        if base != remote_head:
            with revision_files(root, remote_head) as remote:
                require(json.loads(data_bytes(remote["vault.json"]))["id"] == config["vault_id"],
                        "Remote vault identity mismatch")
                remote_derived, _ = derive(canonical(remote))
                require({p: digest(v) for p, v in remote.items() if p.startswith(GENERATED)} ==
                        {p: digest(v) for p, v in remote_derived.items()}, "Remote graph is inconsistent")
                if base == local_head:
                    publish(root, remote, "Fast-forward Atlas", target=remote_head)
                else:
                    with revision_files(root, base) as ancestor:
                        local = canonical(inventory(root))
                        theirs, before = canonical(remote), canonical(ancestor)
                        merged, conflicts = {}, []
                        for path in sorted(local.keys() | theirs.keys() | before.keys()):
                            a, b, c = (digest(m[path]) if path in m else None for m in (before, local, theirs))
                            if b == c or c == a:
                                value = local.get(path)
                            elif b == a:
                                value = theirs.get(path)
                            else:
                                conflicts.append(path)
                                continue
                            if value is not None:
                                merged[path] = value
                        require(not conflicts, "Concurrent edits require reconciliation: " + ", ".join(conflicts))
                        generated, _ = derive(merged)  # Includes deletion versus reference checks.
                        publish(root, {**merged, **generated}, "Merge independent Atlas changes",
                                parents=[local_head, remote_head])
    # No force push; a racing writer results in a rejected push and explicit retry.
    git(root, "push", "origin", "HEAD:refs/heads/" + config["branch"])
    current = head(root)
    observed = git(root, "ls-remote", "origin", "refs/heads/" + config["branch"]).split()
    require(observed and observed[0] == current,
            "Push was issued but exact remote HEAD confirmation is unavailable; rerun sync")
    return {"status": "remote_confirmed", "commit": current, "remote_confirmed": True}


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", help="Defaults to the skill's .local/config.json")
    p.add_argument("--snapshot", help="For paginated reads: reject a changed commit")
    sub = p.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--path")
    init.add_argument("--remote")
    init.add_argument("--branch", default="main")
    init.add_argument("--sync", choices=["manual", "after_write"], default="manual")
    for command in ("status", "verify", "recover", "reindex", "sync", "graph"):
        sub.add_parser(command)
    apply = sub.add_parser("apply")
    apply.add_argument("--plan", required=True)
    ing = sub.add_parser("ingest")
    ing.add_argument("--file", required=True)
    ing.add_argument("--title")
    ing.add_argument("--summary")
    ing.add_argument("--domain", action="append")
    read = sub.add_parser("read")
    read.add_argument("id")
    read.add_argument("--offset", type=int, default=0, help="Zero-based body line offset")
    read.add_argument("--limit", type=int, default=80)
    source = sub.add_parser("source")
    source.add_argument("id")
    source.add_argument("--offset", type=int, default=0)
    source.add_argument("--limit", type=int, default=80)
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


def run(args):
    if args.command == "init":
        return initialize(args)
    config, root = load_config(args)
    with locked(root, recovery=args.command == "recover"):
        if args.command == "recover":
            return recover(root)
        if args.command == "reindex":
            require(not git(root, "diff", "--cached", "--name-only"),
                    "Staged user changes detected; inspect and unstage before reindex")
            files = canonical(inventory(root))
            with revision_files(root, head(root)) as previous:
                old_records = records_from(previous)
            current_records = records_from(files)
            removed = sorted(set(old_records) - set(current_records))
            edited = sorted(rid for rid, r in current_records.items()
                            if rid not in old_records or r["sha256"] != old_records[rid]["sha256"])
            if removed or edited:
                op = uid("op")
                files["operations/" + op + ".json"] = encoded({"id": op, "recorded_at": now(),
                    "changed_ids": edited, "deleted_ids": removed,
                    "deleted_source_hashes": [old_records[r]["meta"]["source"]["sha256"]
                        for r in removed if old_records[r]["meta"]["type"] == "source"]})
                for rid in removed:
                    if old_records[rid]["meta"]["type"] == "source":
                        files.pop(old_records[rid]["meta"]["source"]["raw_path"], None)
            derived, _ = derive(files)
            return finish_write(root, config, publish(root, {**files, **derived}, "Reconcile external Atlas edits"))
        if args.command == "sync":
            return synchronize(root, config)
        if args.command == "apply":
            result = apply_plan(root, json.loads(Path(args.plan).read_text()))
            return finish_write(root, config, result)
        if args.command == "ingest":
            # Freeze the supplied bytes before hashing/validating; an external editor cannot
            # change the archived original halfway through an Atlas transaction.
            source_file = Path(args.file).expanduser().resolve()
            require(source_file.is_file(), "Source file not found")
            with tempfile.TemporaryDirectory(prefix="atlas-import-") as temporary:
                frozen = Path(temporary) / source_file.name
                shutil.copyfile(source_file, frozen)
                args.file = str(frozen)
                return finish_write(root, config, ingest(root, args))
        records = read_snapshot(root, full=args.command == "verify")
        require(not args.snapshot or args.snapshot == head(root),
                "Snapshot changed between reads; restart the affected query")
        if args.command in {"status", "verify", "graph"}:
            graph = json.loads((root / "graph/root.json").read_text())
            return {"status": "valid", "snapshot": head(root), "graph": graph,
                    "remote_configured": bool(config.get("remote")), "remote_checked": False,
                    "sync_mode": config["sync"]}
        if args.command in {"search", "list", "timeline"}:
            return query(root, records, args)
        if args.command == "neighbors":
            return neighbors(root, records, args)
        if args.command in {"read", "source"}:
            require(args.id in records, "Record not found")
            record = records[args.id]
            if args.command == "source":
                require(record["meta"]["type"] == "source", "Not a source record")
                raw = record["meta"]["source"]["raw_path"]
                require(0 < args.limit <= 200 and args.offset >= 0, "Invalid pagination")
                selected_lines, total = [], 0
                try:
                    with (root / raw).open(encoding="utf-8") as stream:
                        for i, line in enumerate(stream):
                            total = i + 1
                            if args.offset <= i < args.offset + args.limit:
                                require(len(line) <= 16000,
                                        "Long source line; use a format-aware parser with stable locators")
                                selected_lines.append({"line": i + 1, "text": line.rstrip("\n")})
                except UnicodeDecodeError:
                    return {"snapshot": head(root), "source_id": args.id, "binary": True,
                            "path": str(root / raw), "sha256": record["meta"]["source"]["sha256"]}
                end = args.offset + args.limit
                return {"snapshot": head(root), "source_id": args.id, "total_lines": total,
                        "lines": selected_lines, "next_offset": end if end < total else None,
                        "truncated": end < total}
            return {"snapshot": head(root), "meta": record["meta"], "sha256": record["sha256"],
                    "body": page_result(record["body"].splitlines(), args)}
        raise AtlasError("Unknown command")


def main():
    args = parser().parse_args()
    try:
        result = run(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (AtlasError, ValueError, KeyError, TypeError, OSError) as e:
        print(json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
