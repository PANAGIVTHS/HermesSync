"""Persistent tool environment tests (no Docker needed)."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

TEMPLATE = Path(__file__).resolve().parents[1] / "tools" / "env.sh"
COMPOSE = Path(__file__).resolve().parents[1] / "docker" / "compose.yaml"


class ToolsEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="hermes-tools-test-")
        self.addCleanup(self.tmp.cleanup)
        self.tools = Path(self.tmp.name)
        for part in ("bin", "env.d", "jdks", "caches/gradle"):
            (self.tools / part).mkdir(parents=True, exist_ok=True)
        self.env_file = self.tools / "env.sh"
        self.env_file.write_bytes(TEMPLATE.read_bytes())

    def shell(self, command: str, login: bool = False):
        env = os.environ.copy()
        env.update({"BASH_ENV": str(self.env_file), "HERMES_TOOLS_HOME": str(self.tools)})
        return subprocess.run(["bash", "-lc" if login else "-c", command],
                              env=env, text=True, capture_output=True, timeout=8, check=True)

    def test_noninteractive_bash_sees_shared_path_and_gradle_cache(self):
        result = self.shell('printf "%s\\n%s\\n" "$PATH" "$GRADLE_USER_HOME"')
        path, gradle = result.stdout.splitlines()
        self.assertEqual(path.split(":")[0], str(self.tools / "bin"))
        self.assertEqual(gradle, str(self.tools / "caches/gradle"))

    def test_bash_login_sees_shared_path(self):
        self.assertIn(str(self.tools / "bin"),
                      self.shell('printf "%s" "$PATH"', login=True).stdout.split(":"))

    def test_env_snippet_and_new_binary_are_available(self):
        (self.tools / "env.d/custom.sh").write_text('export MY_TOOL_HOME="$HERMES_TOOLS_HOME/my-tool"\n')
        exe = self.tools / "bin/my-tool"
        exe.write_text('#!/bin/sh\nprintf "sample-tool"\n')
        exe.chmod(0o755)
        result = self.shell('printf "%s|" "$MY_TOOL_HOME"; my-tool')
        self.assertEqual(result.stdout, f"{self.tools}/my-tool|sample-tool")

    def test_java_switching_is_detected_without_config_edits(self):
        for v in ('21', '25'):
            folder = self.tools / 'jdks' / ('jdk-' + v) / 'bin'
            folder.mkdir(parents=True)
            java = folder / 'java'
            java.write_text('#!/bin/sh\nexit 0\n')
            java.chmod(0o755)
        link = self.tools / 'jdks/current'
        link.symlink_to('jdk-25')
        self.assertEqual(self.shell('printf "%s" "$JAVA_HOME"').stdout,
                         str(link))
        link.unlink()
        link.symlink_to('jdk-21')
        self.assertEqual(self.shell('printf "%s" "$JAVA_HOME"').stdout,
                         str(link))

    def test_repeated_sourcing_does_not_duplicate_path(self):
        result = self.shell('source "$BASH_ENV"; source "$BASH_ENV"; printf "%s" "$PATH"')
        self.assertEqual(result.stdout.split(':').count(str(self.tools / 'bin')), 1)

    def test_tool_environment_is_silent(self):
        result = self.shell(':')
        self.assertEqual(result.stdout, '')
        self.assertEqual(result.stderr, '')

    def test_compose_loads_bash_environment_automatically(self):
        content = COMPOSE.read_text()
        self.assertIn('BASH_ENV: /opt/hermes-tools/env.sh', content)
        self.assertIn('source: ${HERMES_TOOLS_HOME}', content)
        self.assertIn('source: ${HERMES_ACTIVE_WORKSPACE}', content)
        self.assertNotIn('source: ${HERMES_WORKSPACES}\n', content)


if __name__ == '__main__':
    unittest.main()
