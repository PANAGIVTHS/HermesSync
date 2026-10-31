#!/usr/bin/env python3
"""Offline tests for pin-current.sh: never touch the live Hermes runtime."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIGEST = "b" * 64

class PinTest(unittest.TestCase):
    def test_captures_running_image_and_agent_without_restart(self):
        with tempfile.TemporaryDirectory(prefix="hermes-pin-") as td:
            base = Path(td)
            home = base / "home"
            src = home / "Hermes" / "agent-src"
            src.mkdir(parents=True)
            subprocess.run(["git", "init", "-q", str(src)], check=True)
            subprocess.run(["git", "-C", str(src), "config", "user.email", "test@example.com"], check=True)
            subprocess.run(["git", "-C", str(src), "config", "user.name", "Test"], check=True)
            (src / "README").write_text("original agent code")
            subprocess.run(["git", "-C", str(src), "add", "README"], check=True)
            subprocess.run(["git", "-C", str(src), "commit", "-qm", "initial"], check=True)
            sha = subprocess.check_output(["git", "-C", str(src), "rev-parse", "HEAD"], text=True).strip()
            env_file = home / "Hermes" / ".env"
            env_file.write_text("WEBUI_IMAGE=ghcr.io/nesquena/hermes-webui:latest\nHERMES_UID=1000\n")
            repository = base / "repo"
            shutil.copytree(ROOT, repository)
            docker_dir = base / "bin"
            docker_dir.mkdir()
            stub = docker_dir / "docker"
            stub.write_text("#!/bin/sh\n"
                            "if [ \"$1\" = inspect ]; then echo sha256:runningid\n"
                            "elif [ \"$1\" = image ]; then "
                            "echo '[\"ghcr.io/nesquena/hermes-webui@sha256:" + DIGEST + "\"]'\n"
                            "else exit 3\nfi\n")
            stub.chmod(0o755)
            environ = dict(os.environ, HOME=str(home), PATH=str(docker_dir) + os.pathsep + os.environ["PATH"])
            cmd = ["bash", str(repository / "scripts" / "pin-current.sh")]
            subprocess.run(cmd, check=True, env=environ, stdout=subprocess.DEVNULL)
            lock = (repository / "versions.lock").read_text()
            self.assertIn("HERMES_AGENT_COMMIT=" + sha, lock)
            self.assertIn("WEBUI_IMAGE=ghcr.io/nesquena/hermes-webui@sha256:" + DIGEST, lock)
            self.assertIn("HERMES_UID=1000", env_file.read_text())
            self.assertEqual((src / "README").read_text(), "original agent code")
            self.assertEqual(len(list((home / "Hermes").glob(".env.before-pin.*"))), 1)
            subprocess.run(cmd, check=True, env=environ, stdout=subprocess.DEVNULL)
            self.assertEqual(len(list((home / "Hermes").glob(".env.before-pin.*"))), 1)

    def test_missing_lock_rejects_without_mutating_runtime(self):
        with tempfile.TemporaryDirectory(prefix="hermes-pin-") as td:
            repo = Path(td) / "repo"
            shutil.copytree(ROOT, repo)
            self.assertFalse((repo / "versions.lock").exists())
            home = Path(td) / "home"
            home.mkdir()
            p = subprocess.run(["bash", str(repo / "setup.sh"), "--no-start"], env=dict(os.environ, HOME=str(home)), capture_output=True, text=True)
            self.assertNotEqual(p.returncode, 0)
            self.assertFalse((home / "Hermes").exists())

if __name__ == "__main__":
    unittest.main()
