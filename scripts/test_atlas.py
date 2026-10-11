import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parent / "atlas.py"
spec = importlib.util.spec_from_file_location("atlas", SCRIPT)
atlas = importlib.util.module_from_spec(spec)
spec.loader.exec_module(atlas)


class AtlasIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="atlas-tests-")
        self.base = Path(self.tmp.name)
        self.config = self.base / "config.json"
        self.store = self.base / "vault"
        self.cli("init", "--path", str(self.store))
        self.raw = self.base / "input.txt"
        self.raw.write_text("以下全为测试数据。\n小明喜欢茶。\n2024年搬家。\n2026年参加会议。\n")
        self.source = self.cli("ingest", "--file", str(self.raw))["source_id"]

    def tearDown(self):
        self.tmp.cleanup()

    def cli(self, *args, config=None, ok=True, env=None):
        command = [sys.executable, str(SCRIPT), "--config", str(config or self.config), *args]
        process = subprocess.run(command, capture_output=True, text=True, env=env)
        if ok:
            self.assertEqual(process.returncode, 0, process.stderr + process.stdout)
            return json.loads(process.stdout)
        self.assertNotEqual(process.returncode, 0, process.stdout)
        return process

    def record(self, rid, *, title="测试记录", kind="topic", temporal=None, relations=None):
        return {"id": rid, "type": kind, "title": title, "summary": title + "的测试说明",
                "domains": ["life"], "temporal": temporal or {"mode": "none"},
                "basis": "user_statement", "evidence": [{"source": self.source, "locator": "lines:2-4"}],
                "relations": relations or []}

    def put(self, meta, body="测试正文。", old=None):
        return {"action": "put", "meta": meta, "body": body,
                **({"expected_sha256": old["sha256"]} if old else {})}

    def plan(self, changes, config=None):
        snapshot = self.cli("status", config=config)["snapshot"]
        p = self.base / (atlas.uid("plan") + ".json")
        p.write_text(json.dumps({"operation_id": atlas.uid("op"), "base_commit": snapshot,
                                 "changes": changes}, ensure_ascii=False))
        return p

    def apply(self, changes, config=None, ok=True):
        return self.cli("apply", "--plan", str(self.plan(changes, config)), config=config, ok=ok)

    def test_graph_read_and_chinese_short_query(self):
        self.apply([self.put(self.record("person_x", title="小明", kind="person")),
                    self.put(self.record("pref_tea", title="饮茶习惯", kind="preference",
                        relations=[{"type": "about", "target": "person_x"}]), "小明喜欢茶。")])
        found = self.cli("search", "小明")
        self.assertEqual(found["total"], 2)
        graph = self.cli("neighbors", "person_x", "--relation", "about")
        self.assertEqual(graph["items"][0]["from"], "pref_tea")
        self.assertEqual(self.cli("neighbors", "domain:life", "--relation", "in_domain")["total"], 2)
        self.assertEqual(self.cli("verify")["status"], "valid")

    def test_time_rules_order_and_partial_dates(self):
        metas = [self.record("stable"),
                 self.record("old", kind="event", temporal={"mode": "point", "at": "2024"}),
                 self.record("new", kind="event", temporal={"mode": "point", "at": "2026-10"}),
                 self.record("unknown", kind="event", temporal={"mode": "point", "at": None}),
                 self.record("interval", kind="state", temporal={"mode": "interval", "start": "2025", "end": None})]
        self.apply([self.put(m) for m in metas])
        timeline = self.cli("timeline")
        self.assertEqual([x["id"] for x in timeline["items"]], ["new", "interval", "old", "unknown"])
        self.assertEqual(self.cli("timeline", "--since", "2026-10-15")["total"], 1)
        result = self.apply([self.put(self.record("bad", kind="event"))], ok=False)
        self.assertIn("require semantic time", result.stderr)

    def test_timestamp_offsets_and_invalid_interval(self):
        self.apply([self.put(self.record("earlier", kind="event", temporal={"mode": "point", "at": "2026-01-01T10:00:00+08:00"})),
                    self.put(self.record("later", kind="event", temporal={"mode": "point", "at": "2026-01-01T03:00:00Z"}))])
        self.assertEqual([x["id"] for x in self.cli("timeline")["items"]], ["later", "earlier"])
        self.apply([self.put(self.record("bad", kind="state", temporal={"mode": "interval", "start": "2026", "end": "2024"}))], ok=False)

    def test_invalid_graph_is_atomic(self):
        before = self.cli("status")["snapshot"]
        self.apply([self.put(self.record("bad", relations=[{"type": "about", "target": "missing"}]))], ok=False)
        self.assertEqual(self.cli("status")["snapshot"], before)
        self.assertFalse((self.store / "records/topic/bad.md").exists())

    def test_idempotency_and_stale_plans(self):
        p = self.plan([self.put(self.record("first"))])
        q = self.plan([self.put(self.record("second"))])
        self.cli("apply", "--plan", str(p))
        self.assertEqual(self.cli("apply", "--plan", str(p))["status"], "already_applied")
        self.assertIn("Stale base_commit", self.cli("apply", "--plan", str(q), ok=False).stderr)
        self.assertEqual(self.cli("ingest", "--file", str(self.raw))["status"], "already_imported")

    def test_delete_cascade_and_no_resurrection(self):
        self.apply([self.put(self.record("leaf"))])
        source = self.cli("read", self.source)
        delete = {"action": "delete", "id": self.source, "expected_sha256": source["sha256"]}
        self.apply([delete], ok=False)
        leaf = self.cli("read", "leaf")
        self.apply([delete, {"action": "delete", "id": "leaf", "expected_sha256": leaf["sha256"]}])
        self.assertEqual(self.cli("list")["total"], 0)
        self.assertFalse(list((self.store / "sources").rglob("original*")))
        self.assertIn("was deleted", self.cli("ingest", "--file", str(self.raw), ok=False).stderr)
        revived = self.record("leaf")
        revived.update(basis="navigation", evidence=[])
        self.assertIn("Deleted IDs", self.apply([self.put(revived)], ok=False).stderr)

    def test_interruption_before_commit_and_after_commit(self):
        p = self.plan([self.put(self.record("crash"))])
        env = dict(os.environ, ATLAS_TEST_INTERRUPT="after_files")
        self.cli("apply", "--plan", str(p), ok=False, env=env)
        self.assertIn("Interrupted", self.cli("status", ok=False).stderr)
        self.assertEqual(self.cli("recover")["status"], "rolled_back")
        self.cli("read", "crash", ok=False)
        env["ATLAS_TEST_INTERRUPT"] = "after_commit"
        self.cli("apply", "--plan", str(p), ok=False, env=env)
        self.assertEqual(self.cli("recover")["status"], "local_saved")
        self.assertEqual(self.cli("read", "crash")["meta"]["id"], "crash")

    def test_external_edit_reindex_and_stale_graph(self):
        self.apply([self.put(self.record("editable"), "原始正文。")])
        path = self.store / "records/topic/editable.md"
        path.write_text(path.read_text().replace("原始正文。", "已修改正文。"))
        self.cli("search", "已修改", ok=False)
        self.cli("reindex")
        self.assertEqual(self.cli("search", "已修改")["total"], 1)
        path.unlink()
        self.cli("reindex")
        self.assertEqual(self.cli("list", "--type", "topic")["total"], 0)

    def test_coverage_units_and_pagination_snapshot(self):
        source = self.cli("read", self.source)
        meta = source["meta"]
        meta["source"].update(coverage="complete", scope="full file", coverage_note="全部已检查",
            units=[{"id": "u1", "locator": "lines:1-4", "status": "processed", "record_ids": ["fact"]}])
        self.apply([self.put(self.record("fact")), self.put(meta, "来源索引", source)])
        self.assertEqual(self.cli("status")["graph"]["incomplete_sources"], 0)
        first = self.cli("list", "--limit", "1")
        self.assertTrue(first["truncated"])
        second = self.cli("--snapshot", first["snapshot"], "list", "--limit", "1", "--offset", "1")
        self.assertIsNone(second["next_offset"])
        self.apply([self.put(self.record("more"))])
        self.cli("--snapshot", first["snapshot"], "list", ok=False)

    def remote_pair(self):
        remote = self.base / "remote.git"
        subprocess.run(["git", "init", "--bare", "--initial-branch=main", str(remote)], capture_output=True, check=True)
        c = json.loads(self.config.read_text())
        c["remote"] = str(remote)
        self.config.write_text(json.dumps(c))
        subprocess.run(["git", "-C", str(self.store), "remote", "add", "origin", str(remote)], check=True)
        self.assertEqual(self.cli("sync")["status"], "remote_confirmed")
        other = self.base / "other-config.json"
        self.cli("init", "--remote", str(remote), "--path", str(self.base / "other"), config=other)
        return other

    def test_two_clones_independent_merge(self):
        other = self.remote_pair()
        self.apply([self.put(self.record("left"))])
        self.apply([self.put(self.record("right"))], config=other)
        self.cli("sync")
        self.cli("sync", config=other)
        self.cli("sync")
        self.assertEqual({r["id"] for r in self.cli("list", "--type", "topic")["items"]}, {"left", "right"})
        self.assertEqual(self.cli("status")["snapshot"], self.cli("status", config=other)["snapshot"])

    def test_concurrent_same_record_conflict(self):
        self.apply([self.put(self.record("same"))])
        other = self.remote_pair()
        left, right = self.cli("read", "same"), self.cli("read", "same", config=other)
        self.apply([self.put(left["meta"], "左侧修改", left)])
        self.apply([self.put(right["meta"], "右侧修改", right)], config=other)
        self.cli("sync")
        before = self.cli("status", config=other)["snapshot"]
        failure = self.cli("sync", config=other, ok=False)
        self.assertIn("Concurrent edits", failure.stderr)
        self.assertEqual(self.cli("status", config=other)["snapshot"], before)

    def test_symlink_and_path_traversal_rejected(self):
        (self.store / "records/escape.md").symlink_to(self.raw)
        self.cli("verify", ok=False)
        self.assertIn("Symlink", self.cli("reindex", ok=False).stderr)
        (self.store / "records/escape.md").unlink()
        invalid = self.record("../escape")
        self.assertIn("Invalid record ID", self.apply([self.put(invalid)], ok=False).stderr)

    def test_after_write_remote_failure_retains_local_commit(self):
        config = json.loads(self.config.read_text())
        config.update(remote=str(self.base / "missing-remote.git"), sync="after_write")
        self.config.write_text(json.dumps(config))
        subprocess.run(["git", "-C", str(self.store), "remote", "add", "origin", config["remote"]], check=True)
        result = self.apply([self.put(self.record("offline"))])
        self.assertEqual(result["sync"]["status"], "pending_or_conflicted")
        self.assertEqual(self.cli("read", "offline")["meta"]["id"], "offline")

    def test_graph_rebuild_and_read_only_commands(self):
        self.apply([self.put(self.record("kept"))])
        before = self.cli("status")["snapshot"]
        self.cli("search", "测试")
        self.cli("read", "kept")
        self.assertEqual(before, self.cli("status")["snapshot"])
        import shutil
        shutil.rmtree(self.store / "graph")
        self.cli("status", ok=False)
        self.cli("reindex")
        self.assertEqual(self.cli("verify")["status"], "valid")
        self.assertEqual(self.cli("read", "kept")["meta"]["id"], "kept")

    def test_large_graph_pages_and_source_integrity(self):
        # Several graph pages, high-degree source, complete enumeration in one snapshot.
        self.apply([self.put(self.record("item_{:04}".format(i))) for i in range(170)])
        first = self.cli("neighbors", self.source, "--relation", "derived_from", "--limit", "200")
        self.assertEqual(first["total"], 170)
        all_ids, offset, snapshot = set(), 0, first["snapshot"]
        while offset is not None:
            result = self.cli("--snapshot", snapshot, "list", "--type", "topic", "--offset", str(offset), "--limit", "37")
            all_ids.update(x["id"] for x in result["items"])
            offset = result["next_offset"]
        self.assertEqual(len(all_ids), 170)
        source = self.cli("read", self.source)["meta"]["source"]
        self.assertEqual((self.store / source["raw_path"]).read_bytes(), self.raw.read_bytes())


if __name__ == "__main__":
    unittest.main(verbosity=2)
