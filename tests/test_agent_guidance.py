"""Regression tests for globally injected Hermes operating instructions."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from ruamel.yaml import YAML

REPO = Path(__file__).resolve().parents[1]
APPLIER = REPO / 'scripts/apply-agent-guidance.py'
BEGIN = '<!-- BEGIN HERMES PORTABLE WORKFLOW -->'
END = '<!-- END HERMES PORTABLE WORKFLOW -->'


class GuidanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='hermes-guidance-')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / 'data'
        self.home.mkdir()

    def run_applier(self, *extra, okay=True):
        result = subprocess.run([sys.executable, str(APPLIER), '--home', str(self.home), '--repo', str(REPO), *extra], text=True, capture_output=True)
        if okay:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
        return result

    def config(self):
        return YAML(typ='rt').load((self.home / 'config.yaml').read_text())

    def test_fresh_state(self):
        self.run_applier()
        self.assertEqual((self.home / 'SOUL.md').read_text(), (REPO / 'agent/SOUL.md').read_text())
        self.assertEqual((self.home / 'WORKFLOW.md').read_text(), (REPO / 'agent/WORKFLOW.md').read_text())
        prompt = self.config()['agent']['system_prompt']
        self.assertIn('The user—not you—runs host-side review', prompt)
        self.assertEqual(prompt.count(BEGIN), 1)
        self.assertEqual(prompt.count(END), 1)
        self.assertFalse((self.home / 'AGENTS.md').exists())

    def test_idempotent_preserves_user_config_and_auth(self):
        (self.home / 'config.yaml').write_text('model:\n  provider: openai-codex\nagent:\n  reasoning_effort: high\n  system_prompt: "My own extra instructions."\n')
        (self.home / 'SOUL.md').write_text('# Existing personal voice\n')
        (self.home / 'auth.json').write_text('PRIVATE_TOKEN\n')
        self.run_applier()
        current = (self.home / 'config.yaml').read_bytes()
        self.run_applier()
        self.assertEqual((self.home / 'config.yaml').read_bytes(), current)
        self.assertEqual(self.config()['model']['provider'], 'openai-codex')
        self.assertEqual(self.config()['agent']['reasoning_effort'], 'high')
        prompt = self.config()['agent']['system_prompt']
        self.assertTrue(prompt.startswith('My own extra instructions.'))
        self.assertEqual(prompt.count(BEGIN), 1)
        self.assertEqual((self.home / 'SOUL.md').read_text(), '# Existing personal voice\n')
        self.assertEqual((self.home / 'auth.json').read_text(), 'PRIVATE_TOKEN\n')

    def test_custom_workflow_and_explicit_replacement(self):
        self.run_applier()
        (self.home / 'WORKFLOW.md').write_text('User-specific guidance.\n')
        (self.home / 'SOUL.md').write_text('User custom SOUL\n')
        self.run_applier()
        self.assertIn('User-specific guidance.', self.config()['agent']['system_prompt'])
        self.assertEqual((self.home / 'SOUL.md').read_text(), 'User custom SOUL\n')
        self.run_applier('--replace-workflow', '--replace-soul')
        self.assertIn('coding agent inside an isolated Docker', self.config()['agent']['system_prompt'])
        self.assertIn('pragmatic, technically rigorous', (self.home / 'SOUL.md').read_text())
        self.assertTrue(list(self.home.glob('SOUL.md.backup.*')))
        self.assertTrue(list(self.home.glob('WORKFLOW.md.backup.*')))

    def test_invalid_marker_does_not_touch_config(self):
        (self.home / 'config.yaml').write_text('agent:\n  system_prompt: "<!-- BEGIN HERMES PORTABLE WORKFLOW -->"\n')
        before = (self.home / 'config.yaml').read_bytes()
        self.run_applier(okay=False)
        self.assertEqual((self.home / 'config.yaml').read_bytes(), before)

    def test_refuse_symlinked_config(self):
        target = Path(self.temp.name) / 'external.yaml'
        target.write_text('agent: {}\n')
        (self.home / 'config.yaml').symlink_to(target)
        self.run_applier(okay=False)
        self.assertEqual(target.read_text(), 'agent: {}\n')


if __name__ == '__main__':
    unittest.main()
