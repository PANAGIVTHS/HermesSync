"""Upgrade without losing pending agent files or changing real workspace bytes."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

PACKAGE = Path(__file__).resolve().parents[1]

class SafeUpgradeTests(unittest.TestCase):
    def test_upgrade_preserves_pending_work_and_original_git(self):
        with tempfile.TemporaryDirectory(prefix='hermes-safe-upgrade-') as td:
            home = Path(td)
            install = home / 'Hermes'
            src = home / 'repo'; src.mkdir()
            subprocess.run(['git','init','-q',str(src)], check=True)
            (src / '.git' / 'original-metadata').write_text('DO NOT TOUCH')
            (src / 'code.java').write_text('original\n')
            binpath = home / '.local/bin'; binpath.mkdir(parents=True)
            clidir = home / '.local/share/hermes-sync/bin'; clidir.mkdir(parents=True)
            virtual = home / '.local/share/hermes-sync/venv/bin'; virtual.mkdir(parents=True)
            (virtual / 'python').symlink_to(sys.executable)
            target = clidir / 'hermes'
            initial = PACKAGE / 'cli' / 'hermes_sync.py'
            target.write_text(initial.read_text())
            target.chmod(0o755)
            (binpath / 'hermes').symlink_to(target)
            env = dict(os.environ, HOME=str(home), HERMES_INSTALL_DIR=str(install))
            def cli(*args):
                return subprocess.run([sys.executable,str(target),*args],cwd=src,env=env,
                                      capture_output=True,text=True,timeout=45,check=True)
            cli('share','--name','Test','--yes')
            agent=install / 'workspaces/Test'
            (agent/'.gradle').mkdir()
            (agent/'.gradle/cache.bin').write_bytes(b'\x00\x01\x02')
            (agent/'.gitignore').write_text('.gradle/\n')
            (agent/'code.java').write_text('new code from Hermes\n')
            git_before=(src/'.git/original-metadata').read_bytes()
            before={p.relative_to(agent):p.read_bytes() for p in agent.rglob('*') if p.is_file()}
            state = install/'sync/projects/Test'
            state_before={p.relative_to(state):p.read_bytes() for p in state.rglob('*') if p.is_file()}
            updater=subprocess.run([sys.executable,str(PACKAGE/'scripts/upgrade-safely.py'),'--project','Test'],
                cwd=PACKAGE,env=env,capture_output=True,text=True,timeout=90)
            self.assertEqual(updater.returncode,0,updater.stdout+'\n'+updater.stderr)
            self.assertIn('✓ Host sync CLI updated',updater.stdout)
            self.assertEqual(before,{p.relative_to(agent):p.read_bytes() for p in agent.rglob('*') if p.is_file()})
            self.assertEqual(state_before,{p.relative_to(state):p.read_bytes() for p in state.rglob('*') if p.is_file()})
            self.assertEqual((src/'.git/original-metadata').read_bytes(),git_before)
            self.assertEqual((src/'code.java').read_text(),'original\n')
            backups=list((home/'Hermes-backups').iterdir()); self.assertEqual(len(backups),1)
            self.assertEqual((backups[0]/'agent-workspace/.gradle/cache.bin').read_bytes(),b'\x00\x01\x02')
            self.assertTrue((backups[0]/'sync-state/repo.git').is_dir())
            self.assertTrue((backups[0]/'previous-hermes-cli').exists())
            output=cli('status','--verbose').stdout
            self.assertIn('code.java',output)
            self.assertIn('.gitignore',output)
            self.assertNotIn('cache.bin',output)
            cli('sync')
            self.assertEqual((src/'code.java').read_text(),'new code from Hermes\n')
            self.assertFalse((src/'.gradle').exists())
            self.assertTrue((agent/'.gradle/cache.bin').exists())

if __name__ == '__main__': unittest.main(verbosity=2)
