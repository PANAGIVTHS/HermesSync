#!/usr/bin/env python3
"""Project-switch tests using disposable directories and a simulated Docker API."""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parent.parent / 'cli' / 'hermes_sync_v9.py'
COMPOSE = Path(__file__).resolve().parent.parent / 'docker' / 'compose.yaml'


class UseTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='hermes-use-test-')
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name) / 'Hermes'
        (self.base / 'workspaces').mkdir(parents=True)
        (self.base / 'sync' / 'projects').mkdir(parents=True)
        (self.base / 'idle-workspace').mkdir()
        (self.base / 'tools' / 'bin').mkdir(parents=True)
        (self.base / 'tools' / 'bin' / 'saved-tool').write_text('PERSISTENT')
        (self.base / 'compose.yaml').write_text(COMPOSE.read_text())
        (self.base / '.env').write_text(
            f'HERMES_ACTIVE_PROJECT=_inactive\n'
            f'HERMES_ACTIVE_WORKSPACE=\'{self.base / "idle-workspace"}\'\n'
            f'HERMES_TOOLS_HOME=\'{self.base / "tools"}\'\n'
            'WEBUI_PORT=8787\nHERMES_CUSTOM_KEY=keep-this\n')
        for name in ('Game', 'GPU'):
            w = self.base / 'workspaces' / name
            w.mkdir()
            (w / 'code.txt').write_text(name)
            state_dir = self.base / 'sync' / 'projects' / name
            state_dir.mkdir()
            (state_dir / 'repo.git').mkdir()
            (state_dir / 'state.json').write_text(json.dumps({
                'name': name, 'agent': str(w), 'repo': str(state_dir / 'repo.git'),
                'source': str(Path(tmp.name) / 'unrelated-src')
            }))
        fakebin = Path(tmp.name) / 'bin'
        fakebin.mkdir()
        docker = fakebin / 'docker'
        docker.write_text('''#!/usr/bin/env python3
import os,sys
from pathlib import Path
args=sys.argv[1:]
Path(os.environ['DOCKER_LOG']).open('a').write(' '.join(args)+'\\n')
if args[0]=='inspect' and args[1:3]==['-f','{{.State.Running}}']:
    print(os.environ.get('FAKE_RUNNING', 'true'))
    sys.exit(0)
if args[0]=='inspect':
    sys.exit(1)
if os.environ.get('FAKE_DOCKER_FAIL') and args[0]=='compose' and 'up' in args:
    print('simulated recreate failure',file=sys.stderr)
    sys.exit(1)
sys.exit(0)
''')
        docker.chmod(0o755)
        self.log = Path(tmp.name) / 'docker.log'
        self.env = {**os.environ, 'HERMES_INSTALL_DIR': str(self.base),
                    'PATH': str(fakebin) + os.pathsep + os.environ.get('PATH', ''),
                    'DOCKER_LOG': str(self.log), 'NO_COLOR': '1'}

    def cmd(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *args],
                              cwd=self.base.parent, env=self.env, capture_output=True,
                              text=True, timeout=15)

    def active(self):
        return (self.base / '.env').read_text()

    def assert_files_preserved(self):
        for name in ('GPU', 'Game'):
            self.assertEqual((self.base / 'workspaces' / name / 'code.txt').read_text(), name)
        self.assertEqual((self.base / 'tools' / 'bin' / 'saved-tool').read_text(), 'PERSISTENT')
        self.assertIn('HERMES_CUSTOM_KEY=keep-this', self.active())

    def test_switch_one_project_mount_then_other(self):
        self.assertEqual(self.cmd('use', 'Game', '--yes').returncode, 0)
        self.assertIn('HERMES_ACTIVE_PROJECT=\'Game\'', self.active())
        self.assertIn(f'HERMES_ACTIVE_WORKSPACE=\'{self.base / "workspaces" / "Game"}\'', self.active())
        self.assertNotIn('HERMES_ACTIVE_PROJECT=\'GPU\'', self.active())
        self.assertEqual(self.cmd('use', 'GPU', '--yes').returncode, 0)
        self.assertIn("HERMES_ACTIVE_PROJECT='GPU'", self.active())
        self.assert_files_preserved()
        self.assertEqual(self.log.read_text().count('up -d --force-recreate --no-deps webui'), 2)

    def test_unknown_and_unsafe_projects_refused(self):
        for name in ('Missing', '../GPU', '/tmp', '_inactive'):
            self.assertNotEqual(self.cmd('use', name, '--yes').returncode, 0)
        self.assertIn('HERMES_ACTIVE_PROJECT=_inactive', self.active())
        self.assert_files_preserved()

    def test_switch_requires_confirmation_while_running(self):
        p = self.cmd('use', 'Game')
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('confirmation', p.stderr)
        self.assertIn('HERMES_ACTIVE_PROJECT=_inactive', self.active())

    def test_select_none_and_purge_active_is_blocked(self):
        self.assertEqual(self.cmd('use', 'Game', '--yes').returncode, 0)
        p = self.cmd('purge', '--project', 'Game', '--yes')
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('active', p.stderr)
        self.assertTrue((self.base / 'workspaces' / 'Game').is_dir())
        self.assertEqual(self.cmd('use', '--none', '--yes').returncode, 0)
        self.assertIn("HERMES_ACTIVE_PROJECT='_inactive'", self.active())
        self.assert_files_preserved()

    def test_stopped_does_not_start_automatically(self):
        self.env['FAKE_RUNNING'] = 'false'
        p = self.cmd('use', 'Game')
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotIn(' up ', self.log.read_text())
        self.assertIn("HERMES_ACTIVE_PROJECT='Game'", self.active())

    def test_failed_recreation_rolls_back(self):
        self.env['FAKE_DOCKER_FAIL'] = '1'
        p = self.cmd('use', 'Game', '--yes')
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("HERMES_ACTIVE_PROJECT='_inactive'", self.active())
        self.assert_files_preserved()

    def test_missing_workspace_and_symlink_rejected(self):
        import shutil
        shutil.rmtree(self.base / 'workspaces' / 'Game')
        (self.base / 'workspaces' / 'Game').symlink_to(self.base / 'workspaces' / 'GPU', target_is_directory=True)
        self.assertNotEqual(self.cmd('use', 'Game', '--yes').returncode, 0)
        self.assertEqual(self.cmd('use', 'GPU', '--yes').returncode, 0)

    def test_compose_mount_is_specific_and_tools_global(self):
        s = COMPOSE.read_text()
        self.assertIn('source: ${HERMES_ACTIVE_WORKSPACE}', s)
        self.assertIn('target: /workspace/${HERMES_ACTIVE_PROJECT}', s)
        self.assertIn('target: /opt/hermes-tools', s)
        self.assertNotIn('source: ${HERMES_WORKSPACES}', s)


if __name__ == '__main__':
    unittest.main(verbosity=2)
