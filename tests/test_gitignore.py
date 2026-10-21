#!/usr/bin/env python3
"""Tests for Git-standard ignore filtering and preservation of existing work."""
from __future__ import annotations
import json, os, pathlib, subprocess, sys, tempfile, unittest, hashlib
CLI = pathlib.Path(__file__).resolve().parents[1] / 'cli' / 'hermes_sync.py'

class IgnoreTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory(prefix='hermes-ignore-regression-')
        self.addCleanup(self.t.cleanup)
        root = pathlib.Path(self.t.name)
        self.source = root / 'original'
        self.source.mkdir()
        self.install = root / 'Hermes'
        self.agent = self.install / 'workspaces' / 'Sample'
        self.env = dict(os.environ, HERMES_INSTALL_DIR=str(self.install))
        for key in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE'):
            self.env.pop(key, None)

    def put(self, base, name, data):
        path = base / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data)

    def run_cli(self, *args, ok=True):
        p = subprocess.run([sys.executable, str(CLI), *args], cwd=self.source, env=self.env,
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=45)
        self.assertEqual(p.returncode == 0, ok, (args, p.stdout, p.stderr))
        return p.stdout + p.stderr

    def start(self):
        self.run_cli('share', '--name', 'Sample', '--yes')

    def test_pending_agent_gradle_is_ignored_after_gitignore_added(self):
        self.put(self.source, 'src/mod.java', 'original\n')
        self.start()
        self.put(self.agent, '.gitignore', '.gradle/\nbuild/\n')
        self.put(self.agent, '.gradle/9.8/checksums/cache.bin', 'agent cache')
        self.put(self.agent, 'build/libs/mod.jar', 'built output')
        self.put(self.agent, 'src/mod.java', 'modified by Hermes\n')
        output = self.run_cli('status', '--verbose')
        self.assertNotIn('cache.bin', output)
        self.assertNotIn('mod.jar', output)
        self.assertIn('.gitignore', output)
        self.assertIn('mod.java', output)
        self.run_cli('sync')
        self.assertEqual((self.source/'src/mod.java').read_text(), 'modified by Hermes\n')
        self.assertTrue((self.source/'.gitignore').exists())
        self.assertFalse((self.source/'.gradle').exists())
        self.assertFalse((self.source/'build').exists())
        self.assertTrue((self.agent/'.gradle/9.8/checksums/cache.bin').exists())
        self.assertTrue((self.agent/'build/libs/mod.jar').exists())
        self.assertIn('no changes', self.run_cli('status'))

    def test_desktop_generated_cache_hidden_by_new_agent_gitignore(self):
        self.put(self.source, 'src/code.java', 'original\n')
        self.start()
        self.put(self.source, '.gradle/something', 'desktop generated')
        self.put(self.agent, '.gitignore', '.gradle/\n')
        self.assertNotIn('something', self.run_cli('status', '--verbose'))
        self.run_cli('sync')
        self.assertTrue((self.source/'.gradle/something').exists())
        self.assertTrue((self.agent/'.gitignore').exists())
        self.assertFalse((self.agent/'.gradle/something').exists())

    def test_previously_synced_file_does_not_become_deletion(self):
        self.put(self.source, 'build/old.jar', 'baseline artifact')
        self.put(self.source, 'src/a.txt', 'source')
        self.start()
        self.put(self.agent, '.gitignore', 'build/\n')
        self.run_cli('sync')
        self.assertTrue((self.source/'build/old.jar').exists())
        self.assertTrue((self.agent/'build/old.jar').exists())
        self.assertIn('no changes', self.run_cli('status'))

    def test_untracked_files_with_negation_and_nested_gitignore(self):
        self.put(self.source, '.gitignore', '*.log\n!important.log\n')
        self.put(self.source, 'sub/.gitignore', '*.tmp\n!keep.tmp\n')
        self.put(self.source, 'debug.log', 'excluded')
        self.put(self.source, 'important.log', 'included')
        self.put(self.source, 'sub/miss.tmp', 'excluded')
        self.put(self.source, 'sub/keep.tmp', 'included')
        self.start()
        self.assertFalse((self.agent/'debug.log').exists())
        self.assertFalse((self.agent/'sub/miss.tmp').exists())
        self.assertTrue((self.agent/'important.log').exists())
        self.assertTrue((self.agent/'sub/keep.tmp').exists())

    def test_incompatible_gitignore_edits_trigger_regular_conflict(self):
        self.put(self.source, '.gitignore', 'node_modules/\n')
        self.start()
        self.put(self.source, '.gitignore', '.gradle/\n')
        self.put(self.agent, '.gitignore', 'build/\n')
        output = self.run_cli('status', '--verbose')
        self.assertIn('CONFLICT', output)
        self.run_cli('sync', ok=False)
        self.assertEqual((self.source/'.gitignore').read_text(), '.gradle/\n')
        self.assertEqual((self.agent/'.gitignore').read_text(), 'build/\n')

    def test_original_git_metadata_unchanged(self):
        self.put(self.source, 'a.txt', 'first')
        subprocess.run(['git','init','-q',str(self.source)],check=True)
        self.put(self.source, '.gitignore', 'build/\n')
        self.put(self.source, 'build/cache.txt','ignore')
        def digest():
            result=hashlib.sha256()
            for p in sorted((self.source/'.git').rglob('*')):
                if p.is_file():
                    result.update(p.relative_to(self.source).as_posix().encode()+p.read_bytes())
            return result.digest()
        before=digest()
        self.start()
        self.put(self.agent,'a.txt','changed')
        self.run_cli('sync')
        self.assertEqual(before,digest())

    def test_push_and_undo_with_new_ignore(self):
        self.put(self.source, 's.txt', 'v1')
        self.start()
        self.put(self.source,'.gitignore','.gradle/\n')
        self.put(self.source,'s.txt','v2')
        self.put(self.agent,'.gradle/cache','agent build')
        self.run_cli('push')
        self.assertEqual((self.agent/'s.txt').read_text(), 'v2')
        self.assertTrue((self.agent/'.gradle/cache').exists())
        self.run_cli('undo')
        self.assertFalse((self.agent/'.gitignore').exists())
        self.assertEqual((self.agent/'s.txt').read_text(), 'v1')
        self.assertTrue((self.agent/'.gradle/cache').exists())

if __name__ == '__main__': unittest.main(verbosity=2)
