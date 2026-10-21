#!/usr/bin/env python3
"""Black-box regression tests for Hermes Sync v3 (or another compatible CLI).

Runs only in brand-new temporary directories. Does not touch your real projects,
~/Hermes, or any existing private Git sync database.

Usage:
  python3 test_hermes_sync.py --sync ~/.local/bin/hermes-sync
  python3 test_hermes_sync.py --sync ~/.local/bin/hermes-sync --quick
  python3 test_hermes_sync.py --sync ~/.local/bin/hermes-sync --stress 30
"""
from __future__ import annotations

import argparse
import concurrent.futures
import time
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from dataclasses import dataclass

SCRIPT: Path | None = None
STRESS = 8


def digest_tree(root: Path) -> str:
    """Hash contents, permissions, links and paths; include hidden and Git files."""
    h = hashlib.sha256()
    if not root.exists():
        return '<absent>'
    for p in sorted(root.rglob('*')):
        name = p.relative_to(root).as_posix()
        st = p.lstat()
        h.update(name.encode('utf-8', 'surrogateescape') + b'\0')
        h.update(str(stat.S_IFMT(st.st_mode)).encode() + b'\0')
        h.update(str(st.st_mode & 0o111).encode() + b'\0')
        if p.is_symlink():
            h.update(os.fsencode(os.readlink(p)))
        elif p.is_file():
            h.update(p.read_bytes())
        h.update(b'\0')
    return h.hexdigest()


@dataclass
class Result:
    args: tuple[str, ...]
    code: int
    stdout: str
    stderr: str

    @property
    def combined(self) -> str:
        return (self.stdout + '\n' + self.stderr).strip()


class Fixture:
    def __init__(self, base: Path, name: str = 'Sample', initial: dict[str, bytes | str] | None = None):
        self.tmp = base
        self.name = name
        self.source = base / 'actual source'  # spaces in paths are intentional
        self.install = base / 'private Hermes installation'
        self.agent = self.install / 'workspaces' / name
        self.state = self.install / 'sync' / 'projects' / name / 'state.json'
        self.source.mkdir(parents=True)
        self.env = dict(os.environ, HERMES_INSTALL_DIR=str(self.install))
        self.env.pop('GIT_DIR', None)
        self.env.pop('GIT_WORK_TREE', None)
        self.env.pop('GIT_INDEX_FILE', None)
        for name_, contents in (initial or {'alpha.txt': 'start\n'}).items():
            self.put('desktop', name_, contents)

    def put(self, side: str, path: str, contents: bytes | str) -> None:
        root = self.source if side == 'desktop' else self.agent
        p = root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(contents.encode() if isinstance(contents, str) else contents)

    def read(self, side: str, path: str) -> bytes:
        root = self.source if side == 'desktop' else self.agent
        return (root / path).read_bytes()

    def remove(self, side: str, path: str) -> None:
        root = self.source if side == 'desktop' else self.agent
        p = root / path
        if p.is_dir() and not p.is_symlink():
            shutil.rmtree(p)
        else:
            p.unlink()

    def command(self, *args: str, ok: bool = True, cwd: Path | None = None,
                timeout: int = 45) -> Result:
        assert SCRIPT is not None
        p = subprocess.run([sys.executable, str(SCRIPT), *args], cwd=str(cwd or self.source),
                           env=self.env, text=True, capture_output=True, stdin=subprocess.DEVNULL,
                           timeout=timeout)
        result = Result(args, p.returncode, p.stdout, p.stderr)
        if ok and p.returncode != 0:
            raise AssertionError(f'Expected success for {args}: exit={p.returncode}\n{result.combined}')
        if not ok and p.returncode == 0:
            raise AssertionError(f'Expected failure for {args}, but succeeded:\n{result.combined}')
        return result

    def share(self, *extra: str):
        return self.command('share', '--name', self.name, *extra, '--yes')

    def review(self, patch=False, ok=True):
        return self.command('review', *(['--patch'] if patch else []), ok=ok)

    def pull(self, ok=True):
        return self.command('pull', ok=ok)

    def push(self, ok=True):
        return self.command('push', ok=ok)

    def status(self):
        return self.command('status')

    def state_read(self):
        return json.loads(self.state.read_text())

    def state_write(self, state):
        self.state.write_text(json.dumps(state, indent=2) + '\n')


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='hermes-sync-test-')
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def fixture(self, initial=None, name='Sample'):
        return Fixture(self.root, name, initial)

    def test_01_share_dry_run_no_writes(self):
        f = self.fixture({'src/main.sv': 'module m; endmodule\n'})
        before = digest_tree(f.source)
        r = f.command('share', '--name', f.name)
        self.assertIn('DRY RUN', r.combined)
        self.assertEqual(digest_tree(f.source), before)
        self.assertFalse(f.agent.exists())
        self.assertFalse(f.state.exists())

    def test_02_share_nested_and_untracked(self):
        f = self.fixture({'src/main.sv': 'module m; endmodule\n', 'notes/new.txt': 'draft\n', 'empty.txt': ''})
        f.share()
        self.assertEqual(f.read('agent', 'src/main.sv'), f.read('desktop', 'src/main.sv'))
        self.assertEqual(f.read('agent', 'notes/new.txt'), b'draft\n')
        self.assertEqual(f.read('agent', 'empty.txt'), b'')
        self.assertFalse((f.agent / '.git').exists())
        self.assertTrue(f.state.exists())

    def test_03_original_git_repo_is_bitwise_unchanged(self):
        f = self.fixture({'rtl/ALU.sv': 'old\n', 'rtl/Decoder.sv': 'old\n'})
        subprocess.run(['git', 'init', '-q', str(f.source)], check=True)
        subprocess.run(['git', '-C', str(f.source), 'add', 'rtl/ALU.sv'], check=True)
        f.put('desktop', 'rtl/ALU.sv', 'uncommitted edit\n')
        f.put('desktop', 'rtl/untracked.sv', 'untracked\n')
        git_before = digest_tree(f.source / '.git')
        f.share()
        self.assertFalse((f.agent / '.git').exists())
        self.assertEqual(f.read('agent', 'rtl/ALU.sv'), b'uncommitted edit\n')
        self.assertEqual(f.read('agent', 'rtl/untracked.sv'), b'untracked\n')
        f.put('agent', 'rtl/Decoder.sv', 'agent change\n')
        f.review()
        f.pull()
        self.assertEqual(f.read('desktop', 'rtl/Decoder.sv'), b'agent change\n')
        self.assertEqual(digest_tree(f.source / '.git'), git_before,
                         'Original .git metadata/index changed!')

    def test_04_secret_and_dependency_exclusions(self):
        f = self.fixture({'safe.txt': 'keep', '.env': 'SECRET=1', '.env.local': 'SECRET=2',
                          'secret.pem': 'private', 'secret.key': 'private',
                          'node_modules/a.js': 'lib', '.venv/bin/python': 'fake',
                          '__pycache__/m.pyc': 'fake', 'nested/.git/config': 'should skip',
                          'x/id_ed25519': 'key', 'x/.hg/config': 'skip',
                          '.gitignore': '*.txt\n'})
        f.share()
        self.assertTrue((f.agent / 'safe.txt').exists())
        self.assertTrue((f.agent / '.gitignore').exists())
        for path in ('.env', '.env.local', 'secret.pem', 'secret.key', 'node_modules/a.js',
                     '.venv/bin/python', '__pycache__/m.pyc', 'nested/.git/config',
                     'x/id_ed25519', 'x/.hg/config'):
            self.assertFalse((f.agent / path).exists(), f'Leaked excluded path: {path}')

    def test_05_custom_exclusions_at_multiple_depths(self):
        f = self.fixture({'keep/a.sv': 'ok', 'build/junk.bin': 'omit',
                          'rtl/build/x': 'omit', 'logs/a.log': 'omit',
                          'nested/foo.log': 'omit', 'drafts/x.txt': 'omit'})
        f.share('--exclude', 'build/', '--exclude', '*.log', '--exclude', 'drafts/')
        self.assertTrue((f.agent / 'keep/a.sv').exists())
        for p in ('build/junk.bin', 'rtl/build/x', 'logs/a.log', 'nested/foo.log', 'drafts/x.txt'):
            self.assertFalse((f.agent / p).exists())
        f.put('desktop', 'logs/a.log', 'changed secret log')
        f.put('agent', 'keep/a.sv', 'different')
        f.review()
        f.pull()
        self.assertEqual(f.read('desktop', 'logs/a.log'), b'changed secret log')
        self.assertFalse((f.agent / 'logs/a.log').exists())

    def test_06_share_bad_names_rejected(self):
        f = self.fixture()
        for name in ('../escape', '.','..','bad/name', 'x'*100):
            f.command('share', '--name', name, '--yes', ok=False)
        self.assertFalse(f.agent.exists())

    def test_07_reject_share_inside_hermes_base(self):
        f = self.fixture()
        under = f.install / 'bad_source'
        under.mkdir(parents=True)
        (under / 'x.txt').write_text('x')
        f.command('share', str(under), '--name', f.name, '--yes', ok=False)
        self.assertFalse(f.agent.exists())

    def test_08_existing_agent_directory_not_overwritten(self):
        f = self.fixture()
        f.put('agent', 'keep.txt', 'existing')
        f.command('share', '--name', f.name, '--yes', ok=False)
        self.assertEqual(f.read('agent', 'keep.txt'), b'existing')

    def test_09_duplicate_registration_fails(self):
        f = self.fixture()
        f.share()
        orig = digest_tree(f.agent)
        f.command('share', '--name', f.name, '--yes', ok=False)
        self.assertEqual(digest_tree(f.agent), orig)

    def test_10_empty_project(self):
        f = self.fixture({'temporary.txt': ''})
        f.remove('desktop', 'temporary.txt')
        f.share()
        f.review()
        f.pull()
        self.assertEqual(list(f.agent.rglob('*')), [])

    def test_11_unicode_spaces_and_binary_data(self):
        content = bytes(range(256)) * 5
        f = self.fixture({'rtl/αρχείο με κενά.sv': 'αλλάζει\n', 'assets/blob.bin': content})
        f.share()
        self.assertEqual(f.read('agent', 'assets/blob.bin'), content)
        f.put('agent', 'rtl/αρχείο με κενά.sv', 'επεξεργασία\n')
        f.review()
        f.pull()
        self.assertEqual(f.read('desktop', 'rtl/αρχείο με κενά.sv'), 'επεξεργασία\n'.encode())

    def test_12_executable_mode_preserved(self):
        f = self.fixture({'run.sh': '#!/bin/sh\necho hi\n'})
        os.chmod(f.source / 'run.sh', 0o755)
        f.share()
        self.assertTrue((f.agent / 'run.sh').stat().st_mode & stat.S_IXUSR)
        os.chmod(f.agent / 'run.sh', 0o644)
        f.review()
        f.pull()
        self.assertFalse((f.source / 'run.sh').stat().st_mode & stat.S_IXUSR)

    def test_13_first_pull_without_review(self):
        f = self.fixture()
        f.share()
        f.put('agent', 'alpha.txt', 'change\n')
        f.pull()
        self.assertEqual(f.read('desktop', 'alpha.txt'), b'change\n')
        f.command('undo')
        self.assertEqual(f.read('desktop', 'alpha.txt'), b'start\n')

    def test_14_agent_only_change_pull(self):
        f = self.fixture()
        f.share()
        f.put('agent', 'alpha.txt', 'agent edit\n')
        f.review(patch=True)
        f.pull()
        self.assertEqual(f.read('desktop', 'alpha.txt'), b'agent edit\n')
        self.assertEqual(f.read('agent', 'alpha.txt'), b'agent edit\n')

    def test_15_desktop_only_change_pull(self):
        f = self.fixture()
        f.share()
        f.put('desktop', 'alpha.txt', 'desktop edit\n')
        f.review()
        f.pull()
        self.assertEqual(f.read('agent', 'alpha.txt'), b'desktop edit\n')

    def test_16_distinct_file_changes_pull(self):
        f = self.fixture({'a.txt': 'A\n', 'b.txt': 'B\n'})
        f.share()
        f.put('desktop', 'a.txt', 'desktop A\n')
        f.put('agent', 'b.txt', 'agent B\n')
        f.review()
        f.pull()
        for side in ('desktop', 'agent'):
            self.assertEqual(f.read(side, 'a.txt'), b'desktop A\n')
            self.assertEqual(f.read(side, 'b.txt'), b'agent B\n')

    def test_17_nonoverlapping_same_file_lines(self):
        base = ''.join(f'line {i:02}\n' for i in range(50))
        f = self.fixture({'shared.txt': base})
        f.share()
        a = base.replace('line 02', 'DESKTOP 02')
        b = base.replace('line 40', 'HERMES 40')
        f.put('desktop', 'shared.txt', a)
        f.put('agent', 'shared.txt', b)
        f.review()
        f.pull()
        self.assertIn(b'DESKTOP 02', f.read('desktop', 'shared.txt'))
        self.assertIn(b'HERMES 40', f.read('desktop', 'shared.txt'))
        self.assertEqual(f.read('desktop', 'shared.txt'), f.read('agent', 'shared.txt'))

    def test_18_identical_edits_advance_common_base(self):
        f = self.fixture()
        f.share()
        oldbase = f.state_read()['base']
        f.put('desktop', 'alpha.txt', 'both agree\n')
        f.put('agent', 'alpha.txt', 'both agree\n')
        f.review()
        f.pull()
        self.assertNotEqual(f.state_read()['base'], oldbase)
        f.put('agent', 'new.txt', 'later edit')
        f.review()
        f.pull()
        self.assertEqual(f.read('desktop', 'new.txt'), b'later edit')

    def test_19_review_is_nondestructive(self):
        f = self.fixture()
        f.share()
        f.put('agent', 'alpha.txt', 'updated\n')
        a, b = digest_tree(f.source), digest_tree(f.agent)
        f.review(patch=True)
        self.assertEqual((digest_tree(f.source), digest_tree(f.agent)), (a, b))

    def test_20_status_is_nondestructive(self):
        f = self.fixture()
        f.share()
        f.put('agent', 'new.txt', 'test\n')
        a, b = digest_tree(f.source), digest_tree(f.agent)
        result = f.status()
        self.assertIn('Project: Sample', result.stdout)
        self.assertEqual((digest_tree(f.source), digest_tree(f.agent)), (a, b))

    def test_21_stale_review_desktop_side_no_longer_blocks(self):
        f = self.fixture()
        f.share()
        f.put('agent', 'alpha.txt', 'agent change\n')
        f.review()
        f.put('desktop', 'more.txt', 'late local change')
        f.pull()
        self.assertEqual(f.read('desktop', 'more.txt'), b'late local change')
        self.assertEqual(f.read('desktop', 'alpha.txt'), b'agent change\n')

    def test_22_stale_review_agent_side_no_longer_blocks(self):
        f = self.fixture()
        f.share()
        f.put('desktop', 'alpha.txt', 'desktop\n')
        f.review()
        f.put('agent', 'new.txt', 'late agent change')
        f.push()
        self.assertEqual(f.read('desktop', 'alpha.txt'), b'desktop\n')
        self.assertEqual(f.read('agent', 'new.txt'), b'late agent change')

    def test_23_simple_push_keeps_desktop_changes_local_only(self):
        f = self.fixture({'a.txt': 'A\n', 'b.txt': 'B\n'})
        f.share()
        f.put('desktop', 'a.txt', 'edited desktop\n')
        f.put('agent', 'b.txt', 'edited agent\n')
        before = digest_tree(f.source)
        f.review()
        f.push()
        self.assertEqual(digest_tree(f.source), before)
        self.assertEqual(f.read('agent', 'a.txt'), b'edited desktop\n')
        self.assertEqual(f.read('agent', 'b.txt'), b'edited agent\n')
        # PULL after a PUSH should not conflict over edits already incorporated.
        f.review()
        f.pull()
        self.assertEqual(f.read('desktop', 'b.txt'), b'edited agent\n')

    def test_24_agent_edit_after_push_then_pull(self):
        f = self.fixture({'a.txt': 'a\n', 'b.txt': 'b\n'})
        f.share()
        f.put('desktop', 'a.txt', 'desktop a\n')
        f.review(); f.push()
        f.put('agent', 'b.txt', 'agent b\n')
        f.review(); f.pull()
        self.assertEqual(f.read('desktop', 'b.txt'), b'agent b\n')
        self.assertEqual(f.read('desktop', 'a.txt'), b'desktop a\n')

    def test_25_desktop_edit_after_push_then_pull(self):
        f = self.fixture({'a.txt': 'a\n', 'b.txt': 'b\n'})
        f.share()
        f.put('desktop', 'a.txt', 'first edit\n')
        f.review(); f.push()
        f.put('desktop', 'a.txt', 'second edit\n')
        f.put('agent', 'b.txt', 'agent edit\n')
        f.review(); f.pull()
        self.assertEqual(f.read('desktop', 'a.txt'), b'second edit\n')
        self.assertEqual(f.read('agent', 'b.txt'), b'agent edit\n')

    def test_26_pull_undo_restores_diverged_workspaces(self):
        f = self.fixture({'a.txt': 'a\n', 'b.txt': 'b\n'})
        f.share()
        f.put('desktop', 'a.txt', 'desktop\n')
        f.put('agent', 'b.txt', 'agent\n')
        pre_src, pre_agent = digest_tree(f.source), digest_tree(f.agent)
        f.review(); f.pull()
        f.command('undo')
        self.assertEqual(digest_tree(f.source), pre_src)
        self.assertEqual(digest_tree(f.agent), pre_agent)
        f.review(); f.pull()
        self.assertEqual(f.read('desktop', 'b.txt'), b'agent\n')

    def test_27_push_undo_restores_diverged_workspaces(self):
        f = self.fixture({'a.txt': 'a\n', 'b.txt': 'b\n'})
        f.share()
        f.put('desktop', 'a.txt', 'desktop\n')
        f.put('agent', 'b.txt', 'agent\n')
        pre_src, pre_agent = digest_tree(f.source), digest_tree(f.agent)
        f.review(); f.push()
        f.command('undo')
        self.assertEqual(digest_tree(f.source), pre_src)
        self.assertEqual(digest_tree(f.agent), pre_agent)

    def test_28_undo_refuses_new_desktop_edits(self):
        f = self.fixture()
        f.share()
        f.put('agent', 'alpha.txt', 'update\n')
        f.review(); f.pull()
        f.put('desktop', 'alpha.txt', 'after pull\n')
        f.command('undo', ok=False)
        self.assertEqual(f.read('desktop', 'alpha.txt'), b'after pull\n')

    def test_29_undo_refuses_new_agent_edits(self):
        f = self.fixture()
        f.share()
        f.put('agent', 'alpha.txt', 'update\n')
        f.review(); f.pull()
        f.put('agent', 'alpha.txt', 'after pull\n')
        f.command('undo', ok=False)
        self.assertEqual(f.read('agent', 'alpha.txt'), b'after pull\n')

    def test_30_conflict_does_not_modify_workspaces(self):
        f = self.fixture({'settings.txt': 'mode=default\n'})
        f.share()
        f.put('desktop', 'settings.txt', 'mode=desktop\n')
        f.put('agent', 'settings.txt', 'mode=agent\n')
        a, b = digest_tree(f.source), digest_tree(f.agent)
        f.review(ok=False)
        f.pull(ok=False)
        self.assertEqual((digest_tree(f.source), digest_tree(f.agent)), (a, b))

    def test_31_manual_resolve_text_conflict(self):
        f = self.fixture({'settings.txt': 'mode=default\n'})
        f.share()
        f.put('desktop', 'settings.txt', 'mode=desktop\n')
        f.put('agent', 'settings.txt', 'mode=agent\n')
        a, b = digest_tree(f.source), digest_tree(f.agent)
        f.command('resolve')
        self.assertEqual((digest_tree(f.source), digest_tree(f.agent)), (a, b))
        working = f.state.parent / 'resolution' / 'merged' / 'settings.txt'
        self.assertTrue(working.exists())
        self.assertIn(b'<<<<<<<', working.read_bytes())
        f.command('resolve', '--continue', ok=False)
        working.write_text('mode=desktop\nagent_note=agent\n')
        f.command('resolve', '--continue')
        f.review(patch=True)
        f.pull()
        expected = b'mode=desktop\nagent_note=agent\n'
        self.assertEqual(f.read('desktop', 'settings.txt'), expected)
        self.assertEqual(f.read('agent', 'settings.txt'), expected)

    def test_32_manual_resolution_abort(self):
        f = self.fixture({'settings.txt': 'mode=default\n'})
        f.share()
        f.put('desktop', 'settings.txt', 'mode=desktop\n')
        f.put('agent', 'settings.txt', 'mode=agent\n')
        f.command('resolve')
        f.command('resolve', '--abort')
        self.assertFalse((f.state.parent / 'resolution').exists())
        self.assertEqual(f.read('desktop', 'settings.txt'), b'mode=desktop\n')
        self.assertEqual(f.read('agent', 'settings.txt'), b'mode=agent\n')

    def test_33_manual_resolution_stale_after_edit(self):
        f = self.fixture({'settings.txt': 'mode=default\n', 'other.txt': 'original\n'})
        f.share()
        f.put('desktop', 'settings.txt', 'mode=desktop\n')
        f.put('agent', 'settings.txt', 'mode=agent\n')
        f.command('resolve')
        f.put('desktop', 'other.txt', 'late edit\n')
        f.command('resolve', '--continue', ok=False)
        f.command('resolve', '--abort')
        self.assertEqual(f.read('desktop', 'other.txt'), b'late edit\n')

    def test_34_resolved_conflict_cannot_push(self):
        f = self.fixture({'settings.txt': 'mode=default\n'})
        f.share()
        f.put('desktop', 'settings.txt', 'mode=desktop\n')
        f.put('agent', 'settings.txt', 'mode=agent\n')
        f.command('resolve')
        (f.state.parent / 'resolution' / 'merged' / 'settings.txt').write_text('mode=resolved\n')
        f.command('resolve', '--continue')
        f.review()
        f.push(ok=False)
        self.assertEqual(f.read('desktop', 'settings.txt'), b'mode=desktop\n')

    def test_35_additions_both_sides(self):
        f = self.fixture()
        f.share()
        f.put('desktop', 'new/local/a.txt', 'local\n')
        f.put('agent', 'new/agent/b.txt', 'remote\n')
        f.review(); f.pull()
        for side in ('desktop','agent'):
            self.assertEqual(f.read(side, 'new/local/a.txt'), b'local\n')
            self.assertEqual(f.read(side, 'new/agent/b.txt'), b'remote\n')

    def test_36_deletion_and_unrelated_edit(self):
        f = self.fixture({'deleted.txt': 'delete me', 'unchanged.txt': 'before'})
        f.share()
        f.remove('desktop', 'deleted.txt')
        f.put('agent', 'unchanged.txt', 'edited')
        f.review(); f.pull()
        self.assertFalse((f.agent / 'deleted.txt').exists())
        self.assertFalse((f.source / 'deleted.txt').exists())
        self.assertEqual(f.read('desktop', 'unchanged.txt'), b'edited')

    def test_37_file_rename(self):
        f = self.fixture({'old.txt': 'same file\n', 'other.txt': 'old'})
        f.share()
        (f.agent / 'old.txt').rename(f.agent / 'new.txt')
        f.review(); f.pull()
        self.assertFalse((f.source / 'old.txt').exists())
        self.assertEqual(f.read('desktop', 'new.txt'), b'same file\n')

    def test_38_directory_to_file_replacement(self):
        f = self.fixture({'node/child.txt': 'original', 'other.txt': 'preserve'})
        f.share()
        f.remove('agent', 'node')
        f.put('agent', 'node', 'now a file')
        f.review(); f.pull()
        self.assertEqual(f.read('desktop', 'node'), b'now a file')
        self.assertEqual(f.read('desktop', 'other.txt'), b'preserve')

    def test_39_file_to_directory_replacement(self):
        f = self.fixture({'node': 'original'})
        f.share()
        f.remove('agent', 'node')
        f.put('agent', 'node/child.txt', 'now a directory')
        f.review(); f.pull()
        self.assertTrue((f.source / 'node').is_dir())
        self.assertEqual(f.read('desktop', 'node/child.txt'), b'now a directory')

    def test_40_symlink_initial_share_fails_without_copying(self):
        f = self.fixture()
        (f.source / 'link.txt').symlink_to('alpha.txt')
        f.command('share', '--name', f.name, '--yes', ok=False)
        self.assertFalse(f.agent.exists())

    def test_41_symlink_during_sync_refused_and_outside_safe(self):
        f = self.fixture({'a.txt': 'initial'})
        f.share()
        outside = self.root / 'outside.txt'
        outside.write_text('never overwrite')
        (f.agent / 'link.txt').symlink_to(outside)
        f.command('review', ok=False)
        self.assertEqual(outside.read_text(), 'never overwrite')

    def test_42_control_characters_in_filename_rejected(self):
        f = self.fixture()
        f.put('desktop', 'bad\nfilename.txt', 'unsafe')
        f.command('share', '--name', f.name, '--yes', ok=False)
        self.assertFalse(f.agent.exists())

    def test_43_unresolved_binary_conflict_blocks_pull(self):
        f = self.fixture({'data.bin': b'\0before\0'})
        f.share()
        f.put('desktop', 'data.bin', b'\0local\0')
        f.put('agent', 'data.bin', b'\0agent\0')
        f.review(ok=False)
        self.assertEqual(f.read('desktop', 'data.bin'), b'\0local\0')
        self.assertEqual(f.read('agent', 'data.bin'), b'\0agent\0')

    def test_44_delete_modify_conflict(self):
        f = self.fixture({'x.txt': 'initial\n'})
        f.share()
        f.remove('desktop', 'x.txt')
        f.put('agent', 'x.txt', 'agent edit\n')
        f.review(ok=False)
        self.assertFalse((f.source / 'x.txt').exists())
        self.assertEqual(f.read('agent', 'x.txt'), b'agent edit\n')

    def test_45_recover_interrupted_partial_pull(self):
        f = self.fixture({'a.txt': 'A\n', 'b.txt': 'B\n'})
        f.share()
        f.put('desktop', 'a.txt', 'desktop A\n')
        f.put('agent', 'b.txt', 'agent B\n')
        f.review(); f.pull()
        state = f.state_read()
        self.assertIsNotNone(state.get('undo'))
        # Simulate crash AFTER source was modified but BEFORE agent was modified.
        # Current desktop is post-pull, and agent is manually put back in pre-pull state.
        f.put('agent', 'a.txt', 'A\n')
        state['pending_operation'] = 'pull'
        f.state_write(state)
        f.command('recover')
        self.assertEqual(f.read('desktop', 'a.txt'), b'desktop A\n')
        self.assertEqual(f.read('desktop', 'b.txt'), b'B\n')
        self.assertEqual(f.read('agent', 'a.txt'), b'A\n')
        self.assertEqual(f.read('agent', 'b.txt'), b'agent B\n')
        self.assertFalse(f.state_read().get('pending_operation'))

    def test_46_recovery_refuses_unknown_late_edits(self):
        f = self.fixture()
        f.share()
        f.put('agent', 'alpha.txt', 'agent update\n')
        f.review(); f.pull()
        state = f.state_read()
        state['pending_operation'] = 'pull'
        f.state_write(state)
        f.put('desktop', 'alpha.txt', 'NEVER OVERWRITE THIS\n')
        f.command('recover', ok=False)
        self.assertEqual(f.read('desktop', 'alpha.txt'), b'NEVER OVERWRITE THIS\n')
        self.assertTrue(f.state_read().get('pending_operation'))

    def test_47_project_selection_from_other_directory(self):
        f = self.fixture()
        f.share()
        f.put('agent', 'alpha.txt', 'changed\n')
        f.command('review', '--project', f.name, cwd=self.root)
        f.command('pull', '--project', f.name, cwd=self.root)
        self.assertEqual(f.read('desktop', 'alpha.txt'), b'changed\n')

    def test_48_unknown_project_rejected(self):
        f = self.fixture()
        f.share()
        f.command('status', '--project', 'NotRegistered', ok=False)

    def test_49_noop_review_pull(self):
        f = self.fixture()
        f.share()
        before = digest_tree(f.source)
        f.review()
        f.pull()
        self.assertEqual(digest_tree(f.source), before)
        self.assertEqual(digest_tree(f.agent), before)

    def test_50_many_sequential_checkpoints(self):
        f = self.fixture({'a.txt': '0\n', 'b.txt': '0\n'})
        f.share()
        for i in range(1, STRESS+1):
            f.put('agent', 'a.txt', f'{i}\n')
            f.review(); f.pull()
            self.assertEqual(f.read('desktop', 'a.txt'), f'{i}\n'.encode())
            f.put('desktop', 'b.txt', f'{i}\n')
            f.review(); f.push()
            self.assertEqual(f.read('agent', 'b.txt'), f'{i}\n'.encode())
        f.review(); f.pull()
        self.assertEqual(f.read('agent', 'a.txt'), f'{STRESS}\n'.encode())
        self.assertEqual(f.read('desktop', 'b.txt'), f'{STRESS}\n'.encode())

    def test_51_random_nonoverlapping_edits(self):
        rng = random.Random(20261019)
        f = self.fixture({'lines.txt': ''.join(f'{i:04d}: BASE\n' for i in range(100))})
        f.share()
        for iteration in range(STRESS):
            left_line = rng.randrange(2, 24)
            right_line = rng.randrange(70, 95)
            old = f.read('desktop', 'lines.txt').decode()
            lines_l = old.splitlines(keepends=True)
            lines_r = old.splitlines(keepends=True)
            lines_l[left_line] = f'{left_line:04d}: DESKTOP-{iteration}\n'
            lines_r[right_line] = f'{right_line:04d}: AGENT-{iteration}\n'
            f.put('desktop', 'lines.txt', ''.join(lines_l))
            f.put('agent', 'lines.txt', ''.join(lines_r))
            f.review(); f.pull()
            self.assertEqual(f.read('desktop', 'lines.txt'), f.read('agent', 'lines.txt'))
            self.assertIn(f'DESKTOP-{iteration}'.encode(), f.read('desktop', 'lines.txt'))
            self.assertIn(f'AGENT-{iteration}'.encode(), f.read('desktop', 'lines.txt'))


    def test_52_drop_discards_agent_changes_without_review(self):
        f = self.fixture({'rtl/ALU.sv': 'orig\n', 'rtl/Decoder.sv': 'orig\n'})
        f.share()
        f.put('agent', 'rtl/ALU.sv', 'wrong change\n')
        f.put('agent', 'rtl/new.sv', 'new file\n')
        f.remove('agent', 'rtl/Decoder.sv')
        desktop_before = digest_tree(f.source)
        f.command('drop', '--yes')
        self.assertEqual(digest_tree(f.source), desktop_before)
        self.assertEqual(f.read('agent', 'rtl/ALU.sv'), b'orig\n')
        self.assertEqual(f.read('agent', 'rtl/Decoder.sv'), b'orig\n')
        self.assertFalse((f.agent / 'rtl/new.sv').exists())
        self.assertEqual(f.state_read()['base'], f.state_read()['desktop_head'])
        self.assertEqual(f.state_read()['base'], f.state_read()['agent_head'])

    def test_53_drop_undo_restores_prior_divergence(self):
        f = self.fixture({'a.txt': 'original a', 'b.txt': 'original b'})
        f.share()
        f.put('desktop', 'a.txt', 'my fix')
        f.put('agent', 'a.txt', 'hermes bad fix')
        f.put('agent', 'extra.txt', 'new agent file')
        f.remove('agent', 'b.txt')
        before_desktop, before_agent = digest_tree(f.source), digest_tree(f.agent)
        f.command('drop', '--yes')
        f.command('undo')
        self.assertEqual(digest_tree(f.source), before_desktop)
        self.assertEqual(digest_tree(f.agent), before_agent)
        # Original conflict must reappear after undo.
        self.assertIn('CONFLICT', f.status().stdout)

    def test_54_drop_handles_conflict_without_resolution(self):
        f = self.fixture({'settings.txt': 'mode=initial\n'})
        f.share()
        f.put('desktop', 'settings.txt', 'mode=desktop\n')
        f.put('agent', 'settings.txt', 'mode=agent\n')
        f.pull(ok=False)
        f.command('drop', '--yes')
        self.assertEqual(f.read('agent', 'settings.txt'), b'mode=desktop\n')
        self.assertIn('Automatic merge: clean', f.status().stdout)

    def test_55_drop_does_not_reintroduce_rejected_edits(self):
        f = self.fixture({'a.txt': 'base\n', 'b.txt': 'base\n'})
        f.share()
        f.put('agent', 'a.txt', 'rejected\n')
        f.command('drop', '--yes')
        f.put('agent', 'b.txt', 'new accepted change\n')
        f.pull()
        self.assertEqual(f.read('desktop', 'a.txt'), b'base\n')
        self.assertEqual(f.read('desktop', 'b.txt'), b'new accepted change\n')

    def test_56_drop_then_push_without_review(self):
        f = self.fixture({'a.txt': 'base\n', 'b.txt': 'base\n'})
        f.share()
        f.put('agent', 'a.txt', 'rejected edit\n')
        f.command('drop', '--yes')
        f.put('desktop', 'a.txt', 'my new edit\n')
        f.push()
        self.assertEqual(f.read('agent', 'a.txt'), b'my new edit\n')

    def test_57_drop_preserves_original_git_and_untracked(self):
        f = self.fixture({'a.txt': 'initial\n'})
        subprocess.run(['git', 'init', '-q', str(f.source)], check=True)
        subprocess.run(['git', '-C', str(f.source), 'add', 'a.txt'], check=True)
        f.share()
        f.put('desktop', 'untracked.txt', 'desktop new\n')
        f.put('agent', 'a.txt', 'bad\n')
        original_git = digest_tree(f.source / '.git')
        f.command('drop', '--yes')
        self.assertEqual(digest_tree(f.source / '.git'), original_git)
        self.assertFalse((f.agent / '.git').exists())
        self.assertEqual(f.read('agent', 'untracked.txt'), b'desktop new\n')

    def test_58_drop_preserves_excluded_agent_files(self):
        f = self.fixture({'a.txt': 'a\n', 'build/skip.log': 'not tracked\n'})
        f.share('--exclude', 'build/', '--exclude', '*.log')
        f.put('agent', 'build/private.log', 'agent excluded')
        f.put('agent', 'a.txt', 'wrong\n')
        f.put('desktop', 'a.txt', 'correct\n')
        f.command('drop', '--yes')
        self.assertEqual(f.read('agent', 'build/private.log'), b'agent excluded')
        self.assertEqual(f.read('agent', 'a.txt'), b'correct\n')

    def test_59_drop_cancelled_without_confirmation(self):
        f = self.fixture()
        f.share()
        f.put('agent', 'alpha.txt', 'reject\n')
        before = digest_tree(f.agent)
        result = f.command('drop', ok=False)
        self.assertIn('Confirmation required', result.combined)
        self.assertEqual(digest_tree(f.agent), before)
        self.assertIsNone(f.state_read().get('undo'))

    def test_60_drop_refuses_newer_edits_on_undo(self):
        f = self.fixture()
        f.share()
        f.put('agent', 'alpha.txt', 'bad edit\n')
        f.command('drop', '--yes')
        f.put('agent', 'alpha.txt', 'new edit after drop\n')
        f.command('undo', ok=False)
        self.assertEqual(f.read('agent', 'alpha.txt'), b'new edit after drop\n')

    def test_61_drop_discards_unfinished_resolution(self):
        f = self.fixture({'settings.txt': 'mode=initial\n'})
        f.share()
        f.put('desktop', 'settings.txt', 'mode=desktop\n')
        f.put('agent', 'settings.txt', 'mode=agent\n')
        f.command('resolve')
        self.assertTrue((f.state.parent / 'resolution').exists())
        f.command('drop', '--yes')
        self.assertFalse((f.state.parent / 'resolution').exists())
        self.assertIsNone(f.state_read()['resolution'])
        self.assertEqual(f.read('agent', 'settings.txt'), b'mode=desktop\n')

    def test_62_drop_replaces_file_with_dir(self):
        f = self.fixture({'foo': 'desktop file'})
        f.share()
        f.remove('agent', 'foo')
        f.put('agent', 'foo/bar.txt', 'agent directory')
        f.command('drop', '--yes')
        self.assertEqual(f.read('agent', 'foo'), b'desktop file')
        f.command('undo')
        self.assertEqual(f.read('agent', 'foo/bar.txt'), b'agent directory')

    def test_63_drop_replaces_dir_with_file(self):
        f = self.fixture({'foo/bar.txt': 'desktop directory'})
        f.share()
        f.remove('agent', 'foo')
        f.put('agent', 'foo', 'agent file')
        f.command('drop', '--yes')
        self.assertEqual(f.read('agent', 'foo/bar.txt'), b'desktop directory')
        f.command('undo')
        self.assertEqual(f.read('agent', 'foo'), b'agent file')

    def test_64_drop_new_project_selection(self):
        f = self.fixture()
        f.share()
        f.put('agent', 'alpha.txt', 'bad edit\n')
        f.command('drop', '--project', f.name, '--yes', cwd=self.root)
        self.assertEqual(f.read('agent', 'alpha.txt'), b'start\n')

    def test_65_drop_restore_git_history_then_merge(self):
        f = self.fixture({'a.txt': 'a\n', 'b.txt': 'b\n'})
        f.share()
        f.put('desktop', 'a.txt', 'desktop first\n')
        f.push()
        f.put('agent', 'b.txt', 'agent change\n')
        f.command('drop', '--yes')
        f.put('agent', 'b.txt', 'new agent change\n')
        f.pull()
        self.assertEqual(f.read('desktop', 'a.txt'), b'desktop first\n')
        self.assertEqual(f.read('desktop', 'b.txt'), b'new agent change\n')

    def test_66_drop_recovery_interrupted_rollback(self):
        f = self.fixture({'a.txt': 'a\n', 'b.txt': 'b\n'})
        f.share()
        f.put('agent', 'a.txt', 'bad agent edit\n')
        f.put('desktop', 'b.txt', 'desktop edit\n')
        f.command('drop', '--yes')
        state = f.state_read()
        state['pending_operation'] = 'drop'
        f.state_write(state)
        f.command('recover')
        self.assertEqual(f.read('agent', 'a.txt'), b'bad agent edit\n')
        self.assertEqual(f.read('agent', 'b.txt'), b'b\n')
        self.assertEqual(f.read('desktop', 'b.txt'), b'desktop edit\n')
        self.assertFalse(f.state_read().get('pending_operation'))

    def test_67_drop_recovery_refuses_unknown_late_edit(self):
        f = self.fixture()
        f.share()
        f.put('agent', 'alpha.txt', 'bad\n')
        f.command('drop', '--yes')
        state = f.state_read()
        state['pending_operation'] = 'drop'
        f.state_write(state)
        f.put('agent', 'alpha.txt', 'unknown after drop\n')
        f.command('recover', ok=False)
        self.assertEqual(f.read('agent', 'alpha.txt'), b'unknown after drop\n')

    def test_68_noop_drop_preserves_undo(self):
        f = self.fixture()
        f.share()
        f.put('agent', 'alpha.txt', 'agent update\n')
        f.pull()
        self.assertIsNotNone(f.state_read()['undo'])
        f.command('drop', '--yes')
        self.assertIsNotNone(f.state_read()['undo'])
        f.command('undo')
        self.assertEqual(f.read('desktop', 'alpha.txt'), b'start\n')

    def test_69_pull_no_review_still_refuses_conflict(self):
        f = self.fixture({'settings.txt': 'mode=original\n'})
        f.share()
        f.put('agent', 'settings.txt', 'mode=agent\n')
        f.put('desktop', 'settings.txt', 'mode=desktop\n')
        a, b = digest_tree(f.source), digest_tree(f.agent)
        f.pull(ok=False)
        self.assertEqual((digest_tree(f.source), digest_tree(f.agent)), (a,b))

    def test_70_resolved_conflict_pull_without_review(self):
        f = self.fixture({'settings.txt': 'mode=original\n'})
        f.share()
        f.put('agent', 'settings.txt', 'mode=agent\n')
        f.put('desktop', 'settings.txt', 'mode=desktop\n')
        f.command('resolve')
        path = f.state.parent / 'resolution' / 'merged' / 'settings.txt'
        path.write_text('mode=resolved\n')
        f.command('resolve', '--continue')
        f.pull()
        self.assertEqual(f.read('desktop', 'settings.txt'), b'mode=resolved\n')

    def test_71_drop_executable_permissions_and_binary(self):
        f = self.fixture({'script.sh': '#!/bin/sh\necho hello\n', 'bin.dat': bytes(range(256))})
        os.chmod(f.source / 'script.sh', 0o755)
        f.share()
        f.put('agent', 'bin.dat', b'BAD\0BINARY')
        os.chmod(f.agent / 'script.sh', 0o644)
        f.command('drop', '--yes')
        self.assertEqual(f.read('agent', 'bin.dat'), bytes(range(256)))
        self.assertTrue((f.agent / 'script.sh').stat().st_mode & stat.S_IXUSR)



def main() -> int:
    global SCRIPT, STRESS
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--sync', type=Path, default=Path.home() / '.local/bin/hermes-sync',
                        help='Path to the Hermes Sync CLI script (default ~/.local/bin/hermes-sync)')
    parser.add_argument('--quick', action='store_true', help='Skip repeated stress loops (only 1 iteration)')
    parser.add_argument('--stress', type=int, default=8, help='Iterations per stress test (default: 8)')
    parser.add_argument('--verbose', action='store_true', help='Print test names and full results')
    parser.add_argument('--jobs', type=int, default=6, help='Independent disposable projects tested in parallel (default: 6)')
    parser.add_argument('--start', type=int, default=1, help='First numbered scenario to run (default 1)')
    parser.add_argument('--end', type=int, default=999, help='Last numbered scenario to run (default all)')
    opts = parser.parse_args()
    SCRIPT = opts.sync.expanduser().resolve()
    STRESS = 1 if opts.quick else opts.stress
    if STRESS < 1 or STRESS > 1000:
        parser.error('--stress must be between 1 and 1000')
    if not SCRIPT.is_file():
        parser.error(f'Sync script not found: {SCRIPT}')
    check = subprocess.run(['git', '--version'], capture_output=True, text=True)
    if check.returncode:
        parser.error('Git must be installed and available on PATH')
    print('Hermes Sync regression suite')
    print(f'Sync CLI:   {SCRIPT}')
    print(f'Python:     {sys.version.split()[0]}')
    print(f'Git:        {check.stdout.strip()}')
    print(f'Stress:     {STRESS} iterations per stress scenario')
    print('Isolation:  each test gets a new disposable $HERMES_INSTALL_DIR and source directory\n')
    if opts.jobs < 1 or opts.jobs > 32:
        parser.error('--jobs must be between 1 and 32')
    names = [name for name in unittest.defaultTestLoader.getTestCaseNames(SyncTests)
             if opts.start <= int(name.split('_')[1]) <= opts.end]
    if not names:
        parser.error('No scenarios match the requested --start / --end range')

    def execute_test(name):
        instance = SyncTests(name)
        result = unittest.TestResult()
        started = time.monotonic()
        instance.run(result)
        return name, result, time.monotonic() - started

    started = time.monotonic()
    failures, errors, skipped = [], [], []
    with concurrent.futures.ThreadPoolExecutor(max_workers=opts.jobs) as pool:
        futures = [pool.submit(execute_test, name) for name in names]
        for fut in concurrent.futures.as_completed(futures):
            name, result, duration = fut.result()
            success = result.wasSuccessful()
            if opts.verbose or not success:
                print(f'{"PASS" if success else "FAIL"} {name} ({duration:.1f}s)', flush=True)
            else:
                print('.', end='', flush=True)
            failures.extend(result.failures)
            errors.extend(result.errors)
            skipped.extend(result.skipped)
    if not opts.verbose:
        print()
    for test, traceback in failures + errors:
        print(f'\n--- FAILURE: {test.id()} ---\n{traceback}')
    print(f'\nTotal runtime: {time.monotonic()-started:.1f}s with {opts.jobs} parallel workers')
    print(('PASS' if not failures and not errors else 'FAIL') +
          f': {len(names)} scenarios, {len(failures)} failures, {len(errors)} errors, '
          f'{len(skipped)} skipped')
    return 0 if not failures and not errors else 1



if __name__ == '__main__':
    sys.exit(main())
