"""Offline tests for conservative efficiency configuration."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from ruamel.yaml import YAML

REPO = Path(__file__).resolve().parents[1]
TUNER = REPO / 'scripts/tune-efficiency.py'


class EfficiencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='hermes-efficient-')
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / '.hermes'
        self.home.mkdir()
        self.config = self.home / 'config.yaml'
        self.config.write_text('''# Config with credentials left untouched
model:
  provider: openai-codex
  default: gpt-6.1-sol
agent:
  max_turns: 90
  reasoning_effort: high
  system_prompt: "Existing important user prompt"
compression:
  threshold: 0.60
''')
        (self.home / 'auth.json').write_text('secret')

    def run_tuner(self, *args, success=True):
        result = subprocess.run([sys.executable, str(TUNER), '--home', str(self.home), *args], capture_output=True, text=True)
        if success:
            self.assertEqual(0, result.returncode, result.stderr)
        else:
            self.assertNotEqual(0, result.returncode)
        return result

    def test_dry_run_and_apply(self):
        initial = self.config.read_bytes()
        self.run_tuner()
        self.assertEqual(initial, self.config.read_bytes())
        self.run_tuner('--apply')
        cfg = YAML(typ='safe').load(self.config.read_text())
        self.assertEqual(cfg['agent']['max_turns'], 90)
        self.assertEqual(cfg['skills']['creation_nudge_interval'], 0)
        self.assertEqual(cfg['agent']['reasoning_effort'], 'high')
        self.assertEqual(cfg['agent']['system_prompt'], 'Existing important user prompt')
        self.assertEqual(cfg['model']['provider'], 'openai-codex')
        self.assertEqual(cfg['model']['default'], 'gpt-6.1-sol')
        self.assertEqual(cfg['tool_output']['max_bytes'], 16000)
        self.assertEqual(cfg['tool_output']['max_lines'], 400)
        self.assertEqual(cfg['file_read_max_chars'], 50000)
        self.assertEqual(cfg['compression']['threshold'], 0.60)
        self.assertEqual(cfg['compression']['threshold_tokens'], 120000)
        self.assertEqual(cfg['compression']['proactive_prune_tokens'], 48000)
        self.assertEqual((self.home / 'auth.json').read_text(), 'secret')
        backups = list(self.home.glob('config.yaml.backup.efficiency.*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(initial, backups[0].read_bytes())
        current = self.config.read_bytes()
        self.run_tuner('--apply')
        self.assertEqual(current, self.config.read_bytes())
        self.assertEqual(len(list(self.home.glob('config.yaml.backup.efficiency.*'))), 1)

    def test_bad_mapping_is_non_destructive(self):
        self.config.write_text('tool_output: "not a mapping"\n')
        before = self.config.read_bytes()
        self.run_tuner('--apply', success=False)
        self.assertEqual(self.config.read_bytes(), before)
        self.assertFalse(list(self.home.glob('config.yaml.backup.efficiency.*')))

    def test_symlink_refused(self):
        target = self.home / 'actual.yaml'
        target.write_text('agent: {}\n')
        self.config.unlink()
        self.config.symlink_to(target)
        self.run_tuner('--apply', success=False)
        self.assertEqual(target.read_text(), 'agent: {}\n')


if __name__ == '__main__':
    unittest.main()
