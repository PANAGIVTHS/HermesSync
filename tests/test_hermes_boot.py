#!/usr/bin/env python3
"""Offline Docker boot tests: fake docker, isolated runtime, no user data."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parent.parent / 'cli' / 'hermes_sync_v8.py'


class BootTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='hermes-boot-test-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.runtime = self.root / 'Hermes'
        self.runtime.mkdir()
        (self.runtime / 'compose.yaml').write_text('name: hermes\nservices: {}\n')
        (self.runtime / '.env').write_text('WEBUI_PORT=8822\n')
        fakebin = self.root / 'bin'
        fakebin.mkdir()
        docker = fakebin / 'docker'
        docker.write_text('''#!/usr/bin/env python3
import os,sys
from pathlib import Path
Path(os.environ['DOCKER_LOG']).open('a').write(os.getcwd()+'|'+' '.join(sys.argv[1:])+'\\n')
if len(sys.argv)>1 and sys.argv[1]=='inspect':
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
                    'PATH': str(fakebin) + os.pathsep + os.environ.get('PATH', ''),
                    'DOCKER_LOG': str(self.log), 'NO_COLOR': '1'}
        self.project = self.root / 'DesktopProject'
        self.project.mkdir()
        (self.project / '.git').mkdir()
        (self.project / '.git' / 'sentinel').write_text('do not touch')

    def run_boot(self):
        return subprocess.run([sys.executable, str(SCRIPT), 'boot'], cwd=self.project,
                              env=self.env, capture_output=True, text=True, timeout=10)

    def test_boot_starts_compose_from_installed_runtime(self):
        p = self.run_boot()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn('Hermes Docker started.', p.stdout)
        self.assertIn('http://localhost:8822', p.stdout)
        lines = self.log.read_text().splitlines()
        self.assertTrue(any(line.startswith(str(self.runtime) + '|compose --project-directory ')
                            and ' up -d' in line for line in lines))
        self.assertEqual((self.project / '.git' / 'sentinel').read_text(), 'do not touch')

    def test_repeat_boot_is_safe(self):
        self.assertEqual(self.run_boot().returncode, 0)
        self.assertEqual(self.run_boot().returncode, 0)
        self.assertEqual(sum(' up -d' in line for line in self.log.read_text().splitlines()), 2)

    def test_missing_install_is_rejected(self):
        (self.runtime / 'compose.yaml').unlink()
        p = self.run_boot()
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('portable installer', p.stderr)
        self.assertFalse(self.log.exists())

    def test_other_project_container_is_rejected(self):
        self.env['FAKE_EXISTING'] = str(self.root / 'SomeoneElse')
        p = self.run_boot()
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('different Compose project', p.stderr)
        self.assertFalse(any(' up -d' in x for x in self.log.read_text().splitlines()))

    def test_compose_failure_is_reported(self):
        self.env['FAKE_FAIL'] = '1'
        p = self.run_boot()
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('simulated Docker failure', p.stderr)


if __name__ == '__main__':
    unittest.main(verbosity=2)
