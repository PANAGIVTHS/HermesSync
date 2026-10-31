"""Offline smoke tests for the setup repository's WebUI/Agent update path."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
UPDATE = REPO / "update.sh"


class WebUISkillsUpdateTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="hermes-webui-update-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        (self.home / "Hermes").mkdir()
        self.scripts = self.root / "bin"
        self.scripts.mkdir()
        self.repo = self.root / "setup-repo"
        self.repo.mkdir()
        (self.repo / "update.sh").write_text(UPDATE.read_text())
        (self.repo / "setup.sh").write_text(
            '#!/bin/bash\nprintf "%s\\n" "$@" > "$TEST_SETUP_LOG"\n'
        )
        (self.scripts / "docker").write_text(
            '#!/bin/bash\n'
            'if [[ "$1" == compose && "$2" == port ]]; then '
            'printf "127.0.0.1:8787\\n"; exit 0; fi\n'
            'exit 44\n'
        )
        (self.scripts / "curl").write_text(
            '#!/bin/bash\nprintf "%s" "$TEST_CURL_CODE"\n'
        )
        (self.scripts / "sleep").write_text('#!/bin/bash\nexit 0\n')
        for name in ("docker", "curl", "sleep"):
            (self.scripts / name).chmod(0o755)
        (self.repo / "setup.sh").chmod(0o755)
        self.log = self.root / "setup-args.txt"

    def run_update(self, code='200', *args):
        env = os.environ.copy()
        env.update({
            "HOME": str(self.home),
            "PATH": str(self.scripts) + os.pathsep + os.environ["PATH"],
            "TEST_SETUP_LOG": str(self.log),
            "TEST_CURL_CODE": code,
        })
        return subprocess.run(
            ["bash", str(self.repo / "update.sh"), *args],
            env=env, text=True, capture_output=True, timeout=10
        )

    def test_passes_through_workflow_update_and_checks_success(self):
        result = self.run_update('200', '--replace-workflow')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Skills API is healthy', result.stdout)
        self.assertEqual(self.log.read_text().splitlines(),
                         ['--replace-compose', '--replace-workflow'])

    def test_http_500_fails_with_diagnostics(self):
        result = self.run_update('500')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('HTTP 500', result.stderr)
        self.assertIn('logs --tail=100', result.stderr)

    def test_no_start_does_not_probe_or_modify_runtime(self):
        result = self.run_update('500', '--no-start')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('skipped (--no-start)', result.stdout)
        self.assertEqual(self.log.read_text().splitlines(),
                         ['--replace-compose', '--no-start'])


if __name__ == '__main__':
    unittest.main()
