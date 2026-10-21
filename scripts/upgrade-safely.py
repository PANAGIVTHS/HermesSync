#!/usr/bin/env python3
"""Back up an unsynchronized agent project, then update ONLY the host sync CLI.

No Docker operations, file transfers, or project snapshot modifications occur here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile
from datetime import datetime, timezone


def digest_folder(root: Path) -> tuple[int, int, str]:
    """Fingerprint bytes, modes and symlinks without following them."""
    digest = hashlib.sha256()
    files = total = 0
    for p in sorted(root.rglob('*')):
        rel = p.relative_to(root).as_posix()
        st = p.lstat()
        digest.update(os.fsencode(rel) + b'\0')
        digest.update(str(stat.S_IFMT(st.st_mode)).encode() + b'\0')
        digest.update(str(stat.S_IMODE(st.st_mode)).encode() + b'\0')
        if p.is_symlink():
            digest.update(os.fsencode(os.readlink(p)))
        elif p.is_file():
            files += 1
            total += st.st_size
            with p.open('rb') as f:
                while chunk := f.read(1024 * 1024):
                    digest.update(chunk)
        digest.update(b'\0')
    return files, total, digest.hexdigest()


def run() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True, help='Existing Hermes project name')
    args = parser.parse_args()
    name = args.project
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,80}', name):
        parser.error('Invalid project name')

    home = Path.home()
    root = Path(os.environ.get('HERMES_INSTALL_DIR', home / 'Hermes'))
    state_dir = root / 'sync' / 'projects' / name
    agent_dir = root / 'workspaces' / name
    config = state_dir / 'state.json'
    original = Path(__file__).resolve().parents[1] / 'cli' / 'hermes_sync.py'
    target = home / '.local/share/hermes-sync/bin/hermes'
    launcher = home / '.local/bin/hermes'
    interpreter = home / '.local/share/hermes-sync/venv/bin/python'

    for p in (root, root / 'sync', root / 'sync/projects', root / 'workspaces', state_dir, agent_dir):
        if p.is_symlink() or not p.is_dir():
            raise RuntimeError(f'Missing or unsafe project directory: {p}')
    if config.is_symlink() or not config.is_file():
        raise RuntimeError(f'Missing or unsafe project registration: {config}')
    state = json.loads(config.read_text())
    if (state.get('name') != name or Path(state.get('agent', '')) != agent_dir or
            Path(state.get('repo', '')) != state_dir / 'repo.git'):
        raise RuntimeError('Project registration paths do not match; refusing update')
    if state.get('pending_operation'):
        raise RuntimeError('An interrupted sync operation exists; resolve it before upgrading')
    if not original.is_file() or not original.read_bytes().startswith(b'#!'):
        raise RuntimeError('Package CLI source is missing or invalid')
    if not target.is_file() or target.is_symlink() or not interpreter.is_file():
        raise RuntimeError('Existing portable CLI/venv not found; use setup.sh instead')
    if not launcher.is_symlink() or launcher.resolve() != target.resolve():
        raise RuntimeError('Unexpected Hermes launcher; refusing to replace it')
    # Backups are outside workspaces and out of the sync engine's view.
    backup_root = home / 'Hermes-backups'
    if backup_root.is_symlink():
        raise RuntimeError(f'Unsafe backup destination: {backup_root}')
    backup_root.mkdir(mode=0o700, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    dest = Path(tempfile.mkdtemp(prefix=f'pre-gitignore-{name}-{stamp}-', dir=backup_root))
    dest.chmod(0o700)
    print(f'Backing up agent workspace and sync history for {name}...\n  {dest}', flush=True)
    try:
        for label, source in (('agent-workspace', agent_dir), ('sync-state', state_dir)):
            old = digest_folder(source)
            shutil.copytree(source, dest / label, symlinks=True, copy_function=shutil.copy2)
            new = digest_folder(dest / label)
            if old != new:
                raise RuntimeError(f'Backup integrity check failed for {label}; CLI not changed')
            print(f'  Verified {label}: {old[0]} files, {old[1]} bytes', flush=True)
        shutil.copy2(target, dest / 'previous-hermes-cli')
        (dest / 'README.txt').write_text(
            'Pre-.gitignore-sync-upgrade backup.\n'
            f'Project: {name}\n'
            f'Original agent directory: {agent_dir}\n'
            f'Original private sync state: {state_dir}\n'
            'No project files were modified by this upgrade. Restore from backup only after inspecting changes.\n'
        )
        new_code = original.read_text(encoding='utf-8').splitlines(keepends=True)
        new_code[0] = f'#!{interpreter}\n'
        fd, tempname = tempfile.mkstemp(prefix='.hermes-cli-upgrade-', dir=target.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(''.join(new_code))
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tempname, 0o755)
            os.replace(tempname, target)
        finally:
            if os.path.lexists(tempname):
                os.unlink(tempname)
    except Exception:
        print(f'Update stopped. Backup (possibly partial) kept at: {dest}', file=sys.stderr)
        raise
    print('\n✓ Host sync CLI updated. Agent files and Docker were not modified.')
    print(f'✓ Safety backup: {dest}')
    print(f'Next: hermes status --project {name}')
    print('Do NOT synchronize until you have reviewed the status/GUI proposal.')


if __name__ == '__main__':
    try:
        run()
    except Exception as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        sys.exit(1)
