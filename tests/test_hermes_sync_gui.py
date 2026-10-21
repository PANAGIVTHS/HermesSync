#!/usr/bin/env python3
"""Disposable VS Code review regression checks for Hermes Sync v4.

Run: python3 test_hermes_sync_gui.py --sync ~/.local/bin/hermes-sync
A fake `code` executable stands in for VS Code; no editor windows are opened.
All project data is under auto-cleaned temporary directories.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SYNC = None


class GUIReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='hermes-sync-gui-test-')
        root = Path(self.tmp.name)
        self.source = root / 'original project'
        self.source.mkdir()
        (self.source / 'a.txt').write_text('A\n')
        (self.source / 'b.txt').write_text('B\n')
        self.agent = root / 'Hermes' / 'workspaces' / 'Test'
        self.state_dir = root / 'Hermes' / 'sync' / 'projects' / 'Test'
        self.gui = self.state_dir / 'gui-review' / 'working'
        bindir = root / 'bin'
        bindir.mkdir()
        stub = bindir / 'code'
        stub.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$GUI_CAPTURE"\n')
        stub.chmod(0o755)
        self.env = dict(os.environ, HERMES_INSTALL_DIR=str(root / 'Hermes'),
                        GUI_CAPTURE=str(root / 'gui-arguments'), PATH=f'{bindir}:{os.environ["PATH"]}')
        for key in ('GIT_INDEX_FILE','GIT_DIR','GIT_WORK_TREE'):
            self.env.pop(key,None)
        self.run_cli('share', '--name', 'Test', '--yes')

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self,*args,success=True):
        p=subprocess.run([sys.executable,str(SYNC),*args], cwd=self.source,env=self.env,
                         capture_output=True,text=True,stdin=subprocess.DEVNULL,timeout=35)
        self.assertEqual(p.returncode == 0, success, f'{args}\n{p.stdout}\n{p.stderr}')
        return p

    def test_01_edit_proposal_pull_and_undo(self):
        (self.agent/'a.txt').write_text('Hermes\n')
        self.run_cli('review','--gui')
        self.assertTrue((self.gui/'.git').is_dir())
        self.assertEqual((self.gui/'a.txt').read_text(),'Hermes\n')
        self.assertEqual((self.source/'a.txt').read_text(),'A\n')
        (self.gui/'a.txt').write_text('Selective result\n')
        self.run_cli('pull')
        self.assertEqual((self.source/'a.txt').read_text(),'Selective result\n')
        self.assertEqual((self.agent/'a.txt').read_text(),'Selective result\n')
        self.assertFalse(self.gui.exists())
        self.run_cli('undo')
        self.assertEqual((self.source/'a.txt').read_text(),'A\n')
        self.assertEqual((self.agent/'a.txt').read_text(),'Hermes\n')

    def test_02_stale_review_does_not_overwrite(self):
        (self.agent/'a.txt').write_text('Hermes\n')
        self.run_cli('review','--gui')
        (self.source/'b.txt').write_text('Edited after review\n')
        self.run_cli('pull',success=False)
        self.assertEqual((self.source/'a.txt').read_text(),'A\n')
        self.assertEqual((self.agent/'a.txt').read_text(),'Hermes\n')
        self.run_cli('review','--gui',success=False)
        self.run_cli('review','--gui','--reset-gui')
        self.run_cli('review','--abort-gui')
        self.assertFalse(self.gui.exists())

    def test_03_conflict_refuses_until_markers_removed(self):
        (self.agent/'a.txt').write_text('Hermes\n')
        (self.source/'a.txt').write_text('Desktop\n')
        self.run_cli('review','--gui')
        self.assertIn('<<<<<<< ', (self.gui/'a.txt').read_text())
        self.run_cli('pull',success=False)
        self.assertEqual((self.source/'a.txt').read_text(),'Desktop\n')
        (self.gui/'a.txt').write_text('Resolved by hand\n')
        self.run_cli('pull')
        self.assertEqual((self.source/'a.txt').read_text(),'Resolved by hand\n')
        self.assertEqual((self.agent/'a.txt').read_text(),'Resolved by hand\n')

    def test_04_added_file_visible_and_rejectable_in_gui(self):
        (self.agent/'added.sv').write_text('New source\n')
        self.run_cli('review','--gui')
        p=subprocess.run(['git','-C',str(self.gui),'status','--short'],capture_output=True,text=True,check=True)
        self.assertIn('added.sv',p.stdout)
        (self.gui/'added.sv').unlink()
        self.run_cli('pull')
        self.assertFalse((self.source/'added.sv').exists())
        self.assertFalse((self.agent/'added.sv').exists())

    def test_05_reopening_preserves_gui_edits(self):
        (self.agent/'a.txt').write_text('Hermes\n')
        self.run_cli('review','--gui')
        (self.gui/'a.txt').write_text('Edited\n')
        self.run_cli('review','--gui')
        self.assertEqual((self.gui/'a.txt').read_text(),'Edited\n')
        self.run_cli('push',success=False)
        self.run_cli('review','--abort-gui')
        self.run_cli('push')
        self.assertEqual((self.agent/'a.txt').read_text(),'Hermes\n')

    def test_06_real_git_metadata_never_touched(self):
        subprocess.run(['git','init','-q',str(self.source)],check=True)
        subprocess.run(['git','-C',str(self.source),'add','a.txt'],check=True)
        def git_digest():
            h=hashlib.sha256()
            for p in sorted((self.source/'.git').rglob('*')):
                if p.is_file():
                    h.update(p.relative_to(self.source/'.git').as_posix().encode()+p.read_bytes())
            return h.hexdigest()
        before=git_digest()
        (self.agent/'a.txt').write_text('Hermes\n')
        self.run_cli('review','--gui')
        (self.gui/'a.txt').write_text('Changed\n')
        self.run_cli('pull')
        self.assertEqual(git_digest(),before)
        self.assertFalse((self.agent/'.git').exists())

    def test_07_untracked_gitignored_agent_files_not_in_gui(self):
        (self.source/'.gitignore').write_text('*.sv\n')
        self.run_cli('push')  # get .gitignore over to the agent
        (self.agent/'new.sv').write_text('module m; endmodule\n')
        self.run_cli('review','--gui')
        p=subprocess.run(['git','-C',str(self.gui),'status','--short'],capture_output=True,text=True,check=True)
        self.assertNotIn('new.sv',p.stdout)
        self.run_cli('pull')
        self.assertFalse((self.source/'new.sv').exists())
        self.assertTrue((self.agent/'new.sv').exists())

    def test_08_no_change_review_is_cleaned_after_pull(self):
        self.run_cli('review','--gui')
        self.run_cli('pull')
        self.assertFalse(self.gui.exists())


def main():
    global SYNC
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sync',type=Path,default=Path.home()/'.local/bin/hermes-sync')
    args=parser.parse_args()
    SYNC=args.sync.expanduser().resolve()
    if not SYNC.is_file():
        parser.error(f'Cannot find sync CLI: {SYNC}')
    test_suite=unittest.defaultTestLoader.loadTestsFromTestCase(GUIReviewTests)
    result=unittest.TextTestRunner(verbosity=2).run(test_suite)
    return 0 if result.wasSuccessful() else 1


if __name__=='__main__':
    sys.exit(main())
