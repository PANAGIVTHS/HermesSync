#!/usr/bin/env python3
"""Hermes stop: offline tests using fake Docker and disposable runtime files."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parent.parent / 'cli' / 'hermes_sync_v9.py'


class StopTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='hermes-stop-test-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.runtime = self.root / 'Hermes'
        self.runtime.mkdir()
        (self.runtime / 'compose.yaml').write_text('name: hermes\nservices: {}\n')
        (self.runtime / 'workspaces').mkdir()
        (self.runtime / 'workspaces' / 'keep.sv').write_text('DO NOT MODIFY\n')
        (self.runtime / 'sync').mkdir()
        (self.runtime / 'sync' / 'keep.json').write_text('KEEP\n')
        (self.runtime / 'data').mkdir()
        (self.runtime / 'data' / 'auth.json').write_text('KEEP\n')
        self.cwd = self.root / 'OriginalProject'
        self.cwd.mkdir()
        (self.cwd / '.git').mkdir()
        (self.cwd / '.git' / 'keep').write_text('KEEP\n')

        self.bin = self.root / 'bin'
        self.bin.mkdir()
        docker = self.bin / 'docker'
        docker.write_text('''#!/usr/bin/env python3
import os, sys
from pathlib import Path
Path(os.environ['DOCKER_LOG']).open('a').write(os.getcwd() + '|' + ' '.join(sys.argv[1:]) + '\\n')
if len(sys.argv) > 1 and sys.argv[1] == 'inspect':
    if os.environ.get('FAKE_EXISTING'):
        print(os.environ['FAKE_EXISTING'])
        sys.exit(0)
    sys.exit(1)
if os.environ.get('FAKE_FAIL'):
    print('simulated Docker failure', file=sys.stderr)
    sys.exit(1)
sys.exit(0)
''')
        docker.chmod(0o755)
        self.log = self.root / 'docker.log'
        self.env = {**os.environ, 'HERMES_INSTALL_DIR': str(self.runtime),
                    'PATH': str(self.bin) + os.pathsep + os.environ.get('PATH', ''),
                    'DOCKER_LOG': str(self.log), 'NO_COLOR': '1'}

    def run_stop(self):
        return subprocess.run([sys.executable, str(SCRIPT), 'stop'], cwd=self.cwd,
                              env=self.env, capture_output=True, text=True, timeout=10)

    def assert_untouched(self):
        self.assertEqual((self.runtime / 'workspaces' / 'keep.sv').read_text(), 'DO NOT MODIFY\n')
        self.assertEqual((self.runtime / 'sync' / 'keep.json').read_text(), 'KEEP\n')
        self.assertEqual((self.runtime / 'data' / 'auth.json').read_text(), 'KEEP\n')
        self.assertEqual((self.cwd / '.git' / 'keep').read_text(), 'KEEP\n')

    def test_stop_targets_only_hermes_compose(self):
        p = self.run_stop()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn('Hermes Docker stopped.', p.stdout)
        lines = self.log.read_text().splitlines()
        stops = [line for line in lines if '|compose ' in line and line.endswith(' stop')]
        self.assertEqual(len(stops), 1, lines)
        self.assertTrue(stops[0].startswith(str(self.runtime) + '|compose --project-directory '), stops[0])
        self.assertIn(str(self.runtime / 'compose.yaml'), stops[0])
        self.assertFalse(any(' down' in line or ' rm' in line or ' -v' in line for line in lines))
        self.assert_untouched()

    def test_stop_is_repeatable_and_does_not_require_project(self):
        self.assertEqual(self.run_stop().returncode, 0)
        self.assertEqual(self.run_stop().returncode, 0)
        self.assertEqual(sum(line.endswith(' stop') for line in self.log.read_text().splitlines()), 2)
        self.assert_untouched()

    def test_stop_refuses_missing_install(self):
        (self.runtime / 'compose.yaml').unlink()
        p = self.run_stop()
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('portable installer', p.stderr)
        self.assertFalse(self.log.exists())
        self.assert_untouched()

    def test_stop_refuses_foreign_container(self):
        self.env['FAKE_EXISTING'] = str(self.root / 'OtherStack')
        p = self.run_stop()
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('different Compose project', p.stderr)
        self.assertFalse(any(x.endswith(' stop') for x in self.log.read_text().splitlines()))
        self.assert_untouched()

    def test_stop_reports_docker_error(self):
        self.env['FAKE_FAIL'] = '1'
        p = self.run_stop()
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('Docker shutdown failed', p.stderr)
        self.assertIn('simulated Docker failure', p.stderr)
        self.assert_untouched()

    def test_version_stays_v8(self):
        p = subprocess.run([sys.executable, str(SCRIPT), '--version'], cwd=self.cwd,
                           env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn('Hermes Sync v9', p.stdout)
        self.assertIn('stop', subprocess.run([sys.executable, str(SCRIPT), '--help'],
                            cwd=self.cwd, env=self.env, capture_output=True,
                            text=True, timeout=10).stdout)


if __name__ == '__main__':
    unittest.main(verbosity=2)
