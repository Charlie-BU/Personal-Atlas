"""Exercise the host publication contract in a disposable Git object database.

All content is synthetic; this is not a real-account provider acceptance test.
"""
import base64
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from types import SimpleNamespace

import atlas


@unittest.skipUnless(shutil.which('git'), 'Git is optional for disposable publication checks')
class SharedGitTests(unittest.TestCase):
    def test_preserves_foreign_trees_and_rejects_stale_branch_update(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory) / 'objects.git'
            env = {**os.environ, 'GIT_AUTHOR_NAME': 'Synthetic Test',
                   'GIT_AUTHOR_EMAIL': 'test@example.invalid',
                   'GIT_COMMITTER_NAME': 'Synthetic Test',
                   'GIT_COMMITTER_EMAIL': 'test@example.invalid',
                   'GIT_INDEX_FILE': str(Path(directory) / 'index')}
            subprocess.run(['git', 'init', '--bare', '--initial-branch=main', str(repo)],
                           check=True, capture_output=True)

            def git(*args, data=None, check=True):
                return subprocess.run(['git', '--git-dir', str(repo), *args], input=data,
                                      env=env, capture_output=True, check=check)

            def publish(changes, base=None, deletes=(), scoped=True):
                if scoped:
                    self.assertTrue(all(p.startswith('atlas/') for p in [*changes, *deletes]))
                git('read-tree', base or '--empty')
                for path, content in changes.items():
                    blob = git('hash-object', '-w', '--stdin', data=content).stdout.decode().strip()
                    git('update-index', '--add', '--cacheinfo', '100644,' + blob + ',' + path)
                for path in deletes:
                    git('update-index', '--force-remove', '--', path)
                tree = git('write-tree').stdout.decode().strip()
                parents = ['-p', base] if base else []
                commit = git('commit-tree', tree, *parents, data=b'Synthetic update\n').stdout.decode().strip()
                updated = git('update-ref', 'refs/heads/main', commit, base or '0' * 40, check=False)
                return commit, updated.returncode

            def accept(result):
                return publish({p: base64.b64decode(v) for p, v in result['files_base64'].items()},
                               result['base_commit'], result['delete_paths'])

            def snapshot(commit):
                files = {}
                for row in git('ls-tree', '-rz', '--full-tree', commit).stdout.split(b'\0'):
                    if not row:
                        continue
                    meta, path = row.split(b'\t', 1)
                    files[path.decode()] = git('cat-file', 'blob', meta.split()[2].decode()).stdout
                return atlas.Snapshot(files, commit)

            foreign = {'README.md': b'shared', 'health/data.bin': b'\x00\xffhealth',
                       'plans/today.md': b'todo', '.gitignore': b'root rules\n'}
            base, status = publish(foreign, scoped=False)
            self.assertEqual(status, 0)
            initialized, status = accept(atlas.initialize(snapshot(base)))
            self.assertEqual(status, 0)
            imported = atlas.ingest(snapshot(initialized), SimpleNamespace(
                name='fictional.txt', title='Fictional', summary='Synthetic source', domain=['life']),
                b'A fictional record.')
            current, status = accept(imported)
            self.assertEqual(status, 0)
            meta = {'id': 'example', 'type': 'topic', 'title': 'Fictional',
                    'summary': 'Synthetic', 'domains': ['life'], 'basis': 'document',
                    'temporal': {'mode': 'none'}, 'evidence': [
                        {'source': imported['source_id'], 'locator': 'lines:1'}]}
            plan = {'operation_id': 'op_git_scope', 'base_commit': current,
                    'changes': [{'action': 'put', 'meta': meta, 'body': 'Synthetic body.'}]}
            prepared = atlas.apply_plan(snapshot(current), plan)
            advanced, status = publish({'health/new.md': b'other skill'}, current, scoped=False)
            self.assertEqual(status, 0)
            _, status = accept(prepared)
            self.assertNotEqual(status, 0)
            self.assertEqual(git('rev-parse', 'main').stdout.decode().strip(), advanced)
            latest = snapshot(advanced)
            atlas.checked(latest)
            self.assertEqual(dict(atlas.atlas_scope(latest)),
                             dict(atlas.atlas_scope(snapshot(current))))
            rebuilt = {'operation_id': 'op_git_scope', 'base_commit': latest.commit,
                       'changes': [{'action': 'put', 'meta': meta, 'body': 'Synthetic body.'}]}
            final, status = accept(atlas.apply_plan(latest, rebuilt))
            self.assertEqual(status, 0)
            files = snapshot(final)
            self.assertEqual({p: files[p] for p in foreign}, foreign)
            self.assertEqual(files['health/new.md'], b'other skill')
            self.assertIn('atlas/records/topic/example.md', files)
            atlas.checked(files)
