"""Completeness, offline export and hostile-content behavior tests."""
import copy
import json
import shutil
import subprocess
from pathlib import Path
import sys
import tempfile
import unittest
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_atlas
from atlas import AtlasError
from visualization import collect, render, export

class VisualizationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_atlas.AtlasCalculationTests()
        self.fixture.setUp()
        f = self.fixture
        f.apply([f.put(f.record('event_a', 'event', {'mode': 'point', 'at': '2024'})),
                 f.put(f.record('event_b', 'event', {'mode': 'point', 'at': None}))])
        self.data = collect(f.api.snapshot())

    def test_complete_inventory_and_no_remote_mutation(self):
        self.assertEqual(len(self.data['nodes']), self.data['root']['node_count'])
        before = self.fixture.api.head()
        collect(self.fixture.api.snapshot())
        self.assertEqual(before, self.fixture.api.head())

    def test_shared_repository_export_ignores_other_skills_and_prefixes_links(self):
        self.fixture.api.files.update({'health/private.json': b'HEALTH_PRIVATE_VALUE',
                                       'plans/today.md': b'PLAN_PRIVATE_VALUE',
                                       'vault.json': b'not Atlas', 'graph/root.json': b'not Atlas'})
        data = collect(self.fixture.api.snapshot())
        html = render(data)
        self.assertNotIn('HEALTH_PRIVATE_VALUE', html)
        self.assertNotIn('PLAN_PRIVATE_VALUE', html)
        self.assertEqual(data['base_path'], 'atlas/')
        self.assertTrue(all('/atlas/records/' in n['url'] for n in data['nodes'] if n.get('url')))
        self.assertEqual(len(data['nodes']), self.data['root']['node_count'])

    def test_missing_node_edge_and_bad_incoming_rejected(self):
        for key in ['nodes', 'edges']:
            bad = copy.deepcopy(self.data)
            bad[key].pop()
            with self.assertRaises(AtlasError):
                render(bad)
        root = dict(self.fixture.api.snapshot().items())
        class Files(dict):
            commit = 'a' * 40
            def url(self, path):
                return 'https://github.com/example/vault/blob/' + self.commit + '/' + path
        page = next(p for p in root if '/incoming-' in p and json.loads(root[p])['edges'])
        parsed = json.loads(root[page]); parsed['edges'][0]['type'] = 'wrong'
        root[page] = json.dumps(parsed).encode()
        with self.assertRaises(AtlasError):
            collect(Files(root))

    def test_html_escape_and_only_final_file_written(self):
        self.data['nodes'][0]['title'] = '</script><script>alert(1)</script>&'
        html = render(self.data)
        self.assertNotIn('</script><script>alert(1)', html)
        embedded = html.split('<script id="atlas-data" type="application/json">')[1].split('</script>')[0]
        payload = json.loads(embedded)
        self.assertEqual(payload['nodes'][0]['title'], self.data['nodes'][0]['title'])
        self.assertEqual(payload['timeline'], ['event_a', 'event_b'])
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'atlas.html'
            export(self.data, path)
            self.assertEqual([p.name for p in Path(temp).iterdir()], ['atlas.html'])
            with self.assertRaises(AtlasError):
                export(self.data, path)

    def test_service_neutral_links(self):
        for host in ['github.com', 'gitlab.com', 'gitee.com', 'git.example.org']:
            self.data['nodes'][0]['url'] = 'https://' + host + '/user/vault/blob/' + 'a' * 40 + '/record.md'
            render(self.data)

    def test_unsafe_link_rejected(self):
        self.data['nodes'][0]['url'] = 'javascript:alert(1)'
        with self.assertRaises(AtlasError):
            render(self.data)

    def test_inventory_spans_node_and_adjacency_pages(self):
        f = self.fixture
        f.apply([f.put(f.record('topic_' + str(i))) for i in range(82)])
        root = f.api.snapshot()
        graph = json.loads(root['atlas/graph/root.json'])
        domain = json.loads(root['atlas/graph/nodes/domain--life.json'])
        self.assertGreater(len(graph['node_pages']), 1)
        self.assertGreater(len(domain['adjacency']['incoming']), 1)
        complete = collect(root)
        self.assertTrue(all('topic_' + str(i) in {n['id'] for n in complete['nodes']} for i in range(82)))
        self.assertEqual(len(complete['edges']), graph['edge_count'])

    def test_new_exports_embed_shared_theme_and_offline_icons(self):
        # Template-slot strings in a record must remain user data, even on repeated exports.
        self.data['nodes'][0]['title'] = '__ATLAS_ICONS__ </script>'
        for title in ['__ATLAS_ICONS__ </script>', 'A different fresh export']:
            self.data['nodes'][0]['title'] = title
            html = render(self.data)
            embedded = html.split('<script id="atlas-data" type="application/json">')[1].split('</script>')[0]
            self.assertEqual(json.loads(embedded)['nodes'][0]['title'], title)
            icons = html.split('<script id="atlas-icons" type="application/json">')[1].split('</script>')[0]
            parsed = json.loads(icons)
            self.assertTrue(all(value.startswith('data:image/svg+xml;base64,') for value in parsed.values()))
            self.assertTrue({'sun', 'moon', 'compass'}.issubset(parsed))
            self.assertIn('Copyright (c) 2013-2023 Cole Bemis', html)

    @unittest.skipUnless(shutil.which('node'), 'Node is optional for DOM contract tests')
    def test_theme_and_exploration_contracts(self):
        script = Path(__file__).with_name('test_visualization_ui.cjs')
        result = subprocess.run([shutil.which('node'), str(script)], input=render(self.data),
                                text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

if __name__ == '__main__':
    unittest.main()
