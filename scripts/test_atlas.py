"""Remote contract tests with a GitHub API double; all personal data is synthetic and in RAM."""
import base64
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import atlas
from remote_store import APIError, AtlasError, GitHub, RemoteFiles, UnknownOutcome, blob_sha, validate_config

CONFIG = {"schema_version": 2, "provider": "github",
          "remote": "https://github.com/example/personal-atlas.git", "branch": "main",
          "credentials": {"type": "pat", "token": "synthetic-test-token"}}


class FakeGitHub(GitHub):
    def __init__(self):
        super().__init__(copy.deepcopy(CONFIG))
        self.blobs, self.trees, self.commits, self.refs, self.calls = {}, {}, {}, {}, []
        self.fail_at = None
        self.lost_patch_response = False
        self.race = False
        self.truncate = False

    def object(self, collection, value):
        key = hashlib.sha1(atlas.encoded(value)).hexdigest()
        collection[key] = copy.deepcopy(value)
        return key

    def add_blob(self, value):
        sha = blob_sha(value)
        self.blobs[sha] = value
        return sha

    def commit(self, tree, parent=None):
        return self.object(self.commits, {"tree": {"sha": tree}, "parents": [parent] if parent else []})

    def request(self, method, endpoint, payload=None):
        self.calls.append((method, endpoint, copy.deepcopy(payload)))
        if self.fail_at and self.fail_at in endpoint and method != 'GET':
            raise AtlasError("simulated network failure")
        branch = self.branch
        if method == 'GET' and endpoint == '':
            return {"default_branch": "main"}
        if endpoint.startswith('/branches'):
            return [{"name": k} for k in self.refs][:1]
        if endpoint.startswith('/git/ref/heads/'):
            if branch not in self.refs:
                raise APIError(409, 'empty repository')
            return {"object": {"sha": self.refs[branch]}}
        if method == 'PUT' and endpoint == '/contents/vault.json':
            if self.refs:
                raise APIError(422, 'content already exists')
            sha = self.add_blob(base64.b64decode(payload['content']))
            tree = self.object(self.trees, {'vault.json': sha})
            self.refs[branch] = self.commit(tree)
            return {"commit": {"sha": self.refs[branch]}}
        if endpoint == '/git/blobs' and method == 'POST':
            return {'sha': self.add_blob(base64.b64decode(payload['content']))}
        if endpoint.startswith('/git/blobs/'):
            sha = endpoint.rsplit('/', 1)[1]
            return {'encoding': 'base64', 'content': base64.b64encode(self.blobs[sha]).decode()}
        if endpoint == '/git/trees' and method == 'POST':
            tree = dict(self.trees[payload['base_tree']])
            for item in payload['tree']:
                if item['sha'] is None:
                    del tree[item['path']]
                else:
                    tree[item['path']] = item['sha']
            return {'sha': self.object(self.trees, tree)}
        if endpoint.startswith('/git/trees/'):
            sha = endpoint.rsplit('/', 1)[1].split('?')[0]
            entries = self.trees[sha]
            if self.truncate and '?' in endpoint:
                return {'truncated': True, 'tree': []}
            # Nonrecursive fallback has actual subtrees to exercise recursive walking.
            if '?' not in endpoint:
                immediate, subtrees = {}, {}
                for path, value in entries.items():
                    if '/' in path:
                        folder, rest = path.split('/', 1)
                        subtrees.setdefault(folder, {})[rest] = value
                    else:
                        immediate[path] = value
                items = [{'path': p, 'sha': v, 'type': 'blob', 'mode': '100644'} for p, v in immediate.items()]
                items += [{'path': p, 'sha': self.object(self.trees, v), 'type': 'tree', 'mode': '040000'}
                          for p, v in subtrees.items()]
            else:
                items = [{'path': p, 'sha': v, 'type': 'blob', 'mode': '100644'} for p, v in entries.items()]
            return {'truncated': False, 'tree': items}
        if endpoint == '/git/commits' and method == 'POST':
            target = self.object(self.commits, {'tree': {'sha': payload['tree']},
                                              'parents': payload['parents'], 'message': payload['message']})
            return {'sha': target}
        if endpoint.startswith('/git/commits/'):
            return self.commits[endpoint.rsplit('/', 1)[1]]
        if endpoint.startswith('/git/refs/heads/') and method == 'PATCH':
            assert payload['force'] is False
            if self.race:
                current = self.refs[branch]
                self.refs[branch] = self.object(self.commits, {'tree': self.commits[current]['tree'],
                    'parents': [current], 'message': 'concurrent writer'})
                self.race = False
            target = payload['sha']
            if self.refs[branch] not in self.commits[target]['parents']:
                raise APIError(422, 'not a fast-forward')
            self.refs[branch] = target
            if self.lost_patch_response:
                self.lost_patch_response = False
                raise AtlasError('response lost after successful patch')
            return {'object': {'sha': target}}
        raise AssertionError((method, endpoint, payload))


class AtlasRemoteTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeGitHub()
        self.run_cli('init')
        self.source = self.run_cli('ingest', '--stdin', '--name', 'input.txt',
                                  data='测试材料。\n小明喜欢茶。\n2024年搬家。\n'.encode())['source_id']

    def run_cli(self, *argv, data=b''):
        args = atlas.parser().parse_args(argv)
        with patch('sys.stdin', io.TextIOWrapper(io.BytesIO(data), encoding='utf-8')):
            return atlas.run(args, api=self.api)

    def record(self, rid, kind='topic', temporal=None, relations=None):
        return {'id': rid, 'type': kind, 'title': '测试' + rid, 'summary': '测试记录范围',
                'domains': ['life'], 'basis': 'user_statement',
                'temporal': temporal or {'mode': 'none'}, 'relations': relations or [],
                'evidence': [{'source': self.source, 'locator': 'lines:2-3'}]}

    def put(self, meta, body='小明喜欢茶。', old=None):
        return {'action': 'put', 'meta': meta, 'body': body,
                **({'expected_sha256': old['sha256']} if old else {})}

    def plan(self, changes):
        return {'operation_id': atlas.uid('op'), 'base_commit': self.api.head(), 'changes': changes}

    def apply(self, changes):
        return self.run_cli('apply', '--stdin', data=atlas.encoded(self.plan(changes)))

    def test_init_archives_complete_skill_and_excludes_credentials(self):
        files = self.api.snapshot()
        expected = atlas.skill_bundle()
        self.assertGreaterEqual(len(expected), 12)
        self.assertEqual({p: files[p] for p in files if p.startswith(atlas.SKILL_PREFIX)}, expected)
        self.assertFalse(any('.local/' in p or p.endswith('/config.json') for p in files))
        self.assertEqual(self.run_cli('init')['status'], 'already_initialized')
        self.assertEqual(self.run_cli('verify')['status'], 'valid')

    def test_bootstrap_resume(self):
        self.api = FakeGitHub()
        self.api.fail_at = '/git/blobs'
        with self.assertRaises(AtlasError):
            self.run_cli('init')
        self.assertEqual(set(self.api.snapshot()), {'vault.json'})
        self.api.fail_at = None
        self.assertTrue(self.run_cli('init')['remote_confirmed'])
        self.assertEqual(self.run_cli('verify')['status'], 'valid')

    def test_nonatlas_and_missing_branch_rejected(self):
        api = FakeGitHub()
        sha = api.add_blob(b'existing unrelated repository')
        api.refs['main'] = api.commit(api.object(api.trees, {'README.md': sha}))
        with self.assertRaisesRegex(AtlasError, 'not an empty'):
            atlas.initialize(api)
        api.branch = 'missing'
        with self.assertRaisesRegex(AtlasError, 'branch missing'):
            atlas.initialize(api)

    def test_crud_graph_and_source(self):
        self.apply([self.put(self.record('person_x', kind='person')),
                    self.put(self.record('tea', relations=[{'type': 'about', 'target': 'person_x'}]))])
        self.assertEqual(self.run_cli('search', '小明')['total'], 2)
        edges = self.run_cli('neighbors', 'person_x', '--relation', 'about')
        self.assertEqual(edges['items'][0]['from'], 'tea')
        old = self.run_cli('read', 'tea')
        self.apply([self.put(old['meta'], '只在白天喝茶。', old)])
        self.assertEqual(self.run_cli('read', 'tea')['body']['items'], ['只在白天喝茶。'])
        self.assertIn('小明喜欢茶', self.run_cli('source', self.source)['lines'][1]['text'])
        self.assertEqual(self.run_cli('verify')['status'], 'valid')

    def test_dangling_edges_and_hash_conflicts_are_atomic(self):
        before = self.api.head()
        with self.assertRaisesRegex(AtlasError, 'Dangling'):
            self.apply([self.put(self.record('bad', relations=[{'type': 'about', 'target': 'missing'}]))])
        self.assertEqual(self.api.head(), before)
        self.apply([self.put(self.record('kept'))])
        old = self.run_cli('read', 'kept')
        old['sha256'] = 'wrong'
        with self.assertRaisesRegex(AtlasError, 'hash changed'):
            self.apply([self.put(old['meta'], old=old)])

    def test_stale_and_idempotent_operations(self):
        plan = self.plan([self.put(self.record('one'))])
        stale = self.plan([self.put(self.record('two'))])
        self.run_cli('apply', '--stdin', data=atlas.encoded(plan))
        self.assertEqual(self.run_cli('apply', '--stdin', data=atlas.encoded(plan))['status'], 'already_applied')
        with self.assertRaisesRegex(AtlasError, 'Stale'):
            self.run_cli('apply', '--stdin', data=atlas.encoded(stale))
        plan['changes'][0]['body'] = 'different'
        with self.assertRaisesRegex(AtlasError, 'reused'):
            self.run_cli('apply', '--stdin', data=atlas.encoded(plan))

    def test_delete_cascade_and_no_resurrection(self):
        self.apply([self.put(self.record('leaf'))])
        source = self.run_cli('read', self.source)
        delete = {'action': 'delete', 'id': self.source, 'expected_sha256': source['sha256']}
        with self.assertRaises(AtlasError):
            self.apply([delete])
        leaf = self.run_cli('read', 'leaf')
        self.apply([delete, {'action': 'delete', 'id': 'leaf', 'expected_sha256': leaf['sha256']}])
        self.assertFalse(any(p.startswith('sources/') for p in self.api.snapshot()))
        with self.assertRaisesRegex(AtlasError, 'was deleted'):
            self.run_cli('ingest', '--stdin', '--name', 'input.txt', data='测试材料。\n小明喜欢茶。\n2024年搬家。\n'.encode())
        meta = self.record('leaf'); meta.update(basis='navigation', evidence=[])
        with self.assertRaisesRegex(AtlasError, 'Deleted IDs'):
            self.apply([self.put(meta)])

    def test_time_order_precision_and_unknown(self):
        self.apply([self.put(self.record('stable')),
                    self.put(self.record('old', 'event', {'mode': 'point', 'at': '2024'})),
                    self.put(self.record('new', 'event', {'mode': 'point', 'at': '2026-10'})),
                    self.put(self.record('unknown', 'event', {'mode': 'point', 'at': None})),
                    self.put(self.record('stage', 'state', {'mode': 'interval', 'start': '2025', 'end': None}))])
        self.assertEqual([r['id'] for r in self.run_cli('timeline')['items']], ['new', 'stage', 'old', 'unknown'])
        self.assertEqual(self.run_cli('timeline', '--since', '2026-10-15')['total'], 1)
        with self.assertRaisesRegex(AtlasError, 'semantic time'):
            self.apply([self.put(self.record('bad', 'event'))])

    def test_snapshot_pagination_and_lazy_record_reads(self):
        self.apply([self.put(self.record('first')), self.put(self.record('second'))])
        first = self.run_cli('list', '--type', 'topic', '--limit', '1')
        self.assertEqual(first['next_offset'], 1)
        self.api.calls.clear()
        self.run_cli('--snapshot', first['snapshot'], 'list', '--type', 'topic', '--offset', '1')
        files = self.api.snapshot()
        raw_shas = {v['sha'] for p, v in files.entries.items() if p.startswith(('records/', 'sources/'))}
        self.assertFalse(any(ep.startswith('/git/blobs/') and ep.rsplit('/', 1)[1] in raw_shas
                             for _, ep, _ in self.api.calls))
        self.apply([self.put(self.record('third'))])
        with self.assertRaisesRegex(AtlasError, 'Snapshot changed'):
            self.run_cli('--snapshot', first['snapshot'], 'list')

    def test_truncated_tree_inventory_is_complete(self):
        expected = set(self.api.snapshot())
        self.api.truncate = True
        self.assertEqual(set(self.api.snapshot()), expected)
        self.assertEqual(self.run_cli('verify')['status'], 'valid')

    def test_failed_upload_does_not_publish_partial_graph(self):
        before = self.api.head()
        self.api.fail_at = '/git/trees'
        with self.assertRaises(AtlasError):
            self.apply([self.put(self.record('unpublished'))])
        self.assertEqual(self.api.head(), before)
        self.api.fail_at = None
        self.assertEqual(self.run_cli('verify')['status'], 'valid')

    def test_lost_response_is_confirmed_and_retry_is_idempotent(self):
        plan = self.plan([self.put(self.record('kept'))])
        self.api.lost_patch_response = True
        result = self.run_cli('apply', '--stdin', data=atlas.encoded(plan))
        self.assertTrue(result['remote_confirmed'])
        self.assertEqual(self.run_cli('apply', '--stdin', data=atlas.encoded(plan))['status'], 'already_applied')

    def test_racing_writer_is_not_overwritten(self):
        self.api.race = True
        with self.assertRaises(UnknownOutcome):
            self.apply([self.put(self.record('racing'))])
        self.assertNotIn('records/topic/racing.md', self.api.snapshot())
        self.assertEqual(self.api.commits[self.api.head()]['message'], 'concurrent writer')

    def test_reindex_and_archive_skill(self):
        files = self.api.snapshot()
        damaged = dict(files)
        damaged.pop('graph/root.json')
        self.api.publish(files, damaged, 'synthetic external graph removal')
        with self.assertRaises(AtlasError):
            self.run_cli('verify')
        self.assertTrue(self.run_cli('reindex')['remote_confirmed'])
        self.assertEqual(self.run_cli('archive-skill')['status'], 'unchanged')
        self.assertEqual(self.run_cli('verify')['status'], 'valid')

    def test_no_personal_data_disk_writes(self):
        def forbidden(*args, **kwargs):
            raise AssertionError('Unexpected disk write')
        with patch('os.open', forbidden), patch.object(Path, 'write_bytes', forbidden), \
             patch.object(Path, 'write_text', forbidden), patch.object(Path, 'mkdir', forbidden):
            self.apply([self.put(self.record('ram_only'))])
            self.run_cli('source', self.source)
            self.run_cli('verify')
            self.run_cli('archive-skill')

    def test_config_plaintext_only_runtime_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '.local/config.json'
            args = atlas.parser().parse_args(['--config', str(path), 'configure', '--stdin'])
            with patch('sys.stdin', io.StringIO(json.dumps(CONFIG))):
                result = atlas.configure(args)
            self.assertEqual(json.loads(path.read_text())['credentials']['token'], CONFIG['credentials']['token'])
            self.assertNotIn(CONFIG['credentials']['token'], json.dumps(result))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual([p for p in Path(directory).rglob('*') if p.is_file()], [path])
        self.assertEqual(atlas.config_path(atlas.parser().parse_args(['status'])), atlas.SKILL / '.local/config.json')
        with self.assertRaises(AtlasError):
            validate_config({**CONFIG, 'checkout': '/tmp/local'})
        with self.assertRaises(AtlasError):
            validate_config({**CONFIG, 'remote': 'https://token@github.com/example/repo'})

    def test_auth_header_and_error_redaction(self):
        api = GitHub(CONFIG)
        captured = []
        class Opener:
            def open(self, request, timeout):
                captured.append(request)
                raise OSError('error ' + CONFIG['credentials']['token'])
        api.opener = Opener()
        with self.assertRaises(AtlasError) as caught:
            api.request('GET', '')
        self.assertNotIn(CONFIG['credentials']['token'], str(caught.exception))
        self.assertEqual(captured[0].get_header('Authorization'), 'Bearer ' + CONFIG['credentials']['token'])

    def test_binary_source_and_duplicate_ingest(self):
        raw = b'\xff\x00test'
        result = self.run_cli('ingest', '--stdin', '--name', 'test.bin', data=raw)
        self.assertEqual(self.run_cli('ingest', '--stdin', '--name', 'test.bin', data=raw)['status'], 'already_imported')
        response = self.run_cli('source', result['source_id'])
        self.assertTrue(response['binary'])
        self.assertIsNone(response['local_file'])
        self.assertIn('/blob/', response['url'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
