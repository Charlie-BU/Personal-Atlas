"""Pure calculation contracts; synthetic files in RAM, no remote client."""
import base64
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import atlas
from atlas import AtlasError


class MemoryFiles:
    """Test fixture simulating the agent accepting a prepared changeset."""
    def __init__(self):
        self.files, self.commit, self.calls = {}, None, []

    def head(self):
        return self.commit

    def snapshot(self):
        return atlas.Snapshot(dict(self.files), self.commit,
            {p: 'https://git.example.org/user/vault/blob/' + self.commit + '/' + p
             for p in self.files} if self.commit else {})

    def accept(self, result):
        if result.get('status') != 'prepared':
            return
        assert result['base_commit'] == self.commit
        for p in result['delete_paths']:
            self.files.pop(p)
        self.files.update({p: base64.b64decode(v) for p, v in result['files_base64'].items()})
        self.commit = atlas.digest(atlas.encoded({p: atlas.digest(v) for p, v in self.files.items()}))[:40]


class AtlasCalculationTests(unittest.TestCase):
    def setUp(self):
        self.api = MemoryFiles()
        self.run_cli('init')
        self.source = self.run_cli('ingest', '--name', 'input.txt',
                                  data='测试材料。\n小明喜欢茶。\n2024年搬家。\n'.encode())['source_id']

    def run_cli(self, *argv, data=b''):
        argv = tuple(a for a in argv if a != '--stdin')
        args = atlas.parser().parse_args(['--stdin', *argv])
        root = self.api.snapshot()
        payload = {'snapshot': root.commit,
                   'files_base64': {p: base64.b64encode(v).decode() for p, v in root.items()},
                   'urls': root.urls}
        if args.command == 'apply':
            payload['plan'] = json.loads(data)
        if args.command == 'ingest':
            payload['source_base64'] = base64.b64encode(data).decode()
        result = atlas.run(args, payload)
        self.api.accept(result)
        return result

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
        return self.run_cli('apply', data=atlas.encoded(self.plan(changes)))

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

    def test_binary_source_and_duplicate_ingest(self):
        raw = b'\xff\x00test'
        result = self.run_cli('ingest', '--stdin', '--name', 'test.bin', data=raw)
        self.assertEqual(self.run_cli('ingest', '--stdin', '--name', 'test.bin', data=raw)['status'], 'already_imported')
        response = self.run_cli('source', result['source_id'])
        self.assertTrue(response['binary'])
        self.assertIsNone(response['local_file'])
        self.assertIn('/blob/', response['url'])


    def test_preparation_does_not_publish_or_require_credentials(self):
        root = self.api.snapshot()
        plan = self.plan([self.put(self.record('ready'))])
        result = atlas.apply_plan(root, plan)
        self.assertEqual(result['status'], 'prepared')
        self.assertFalse(result['remote_confirmed'])
        self.assertEqual(self.api.head(), root.commit)
        self.assertNotIn('records/topic/ready.md', root)
        self.assertIn('records/topic/ready.md', result['files_base64'])
        self.assertTrue(any(p.startswith('graph/') for p in result['files_base64']))

    def test_init_unique_ids_and_nonatlas_rejection(self):
        one = atlas.initialize(atlas.Snapshot({}, None))
        two = atlas.initialize(atlas.Snapshot({}, None))
        self.assertNotEqual(one['vault_id'], two['vault_id'])
        self.assertFalse(one['remote_confirmed'])
        with self.assertRaises(AtlasError):
            atlas.initialize(atlas.Snapshot({'README.md': b'unrelated'}, 'a' * 40))

    def test_stdin_pipeline_has_no_network_or_data_file_writes(self):
        root = self.api.snapshot()
        payload = {'snapshot': root.commit,
                   'files_base64': {p: base64.b64encode(v).decode() for p, v in root.items()},
                   'plan': self.plan([self.put(self.record('pipeline'))])}
        args = atlas.parser().parse_args(['--stdin', 'apply'])
        with patch('sys.stdin', io.StringIO(json.dumps(payload))), \
             patch('socket.socket', side_effect=AssertionError('network attempted')), \
             patch.object(Path, 'write_bytes', side_effect=AssertionError('data written')), \
             patch.object(Path, 'write_text', side_effect=AssertionError('data written')):
            self.assertEqual(atlas.run(args)['status'], 'prepared')
        damaged = self.api.snapshot()
        damaged.pop('graph/root.json')
        with self.assertRaises(AtlasError):
            atlas.apply_plan(damaged, payload['plan'])

    def test_snapshot_and_path_validation(self):
        with self.assertRaises(AtlasError):
            atlas.Snapshot({'../outside': b''}, 'a' * 40)
        args = atlas.parser().parse_args(['--stdin', '--snapshot', 'b' * 40, 'status'])
        with self.assertRaisesRegex(AtlasError, 'Snapshot mismatch'):
            atlas.run(args, {'snapshot': 'a' * 40, 'files_base64': {}})


if __name__ == '__main__':
    unittest.main()
