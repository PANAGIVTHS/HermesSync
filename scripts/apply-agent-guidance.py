#!/usr/bin/env python3
"""Install instance-level Hermes context without modifying any project copies.

The user-owned workflow file is authoritative. A marked section is injected into
agent.system_prompt, preserving any unrelated prompt and the rest of config.yaml.
"""
from __future__ import annotations

import argparse
import io
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.scalarstring import LiteralScalarString

BEGIN = '<!-- BEGIN HERMES PORTABLE WORKFLOW -->'
END = '<!-- END HERMES PORTABLE WORKFLOW -->'


def atomic_write(path: Path, data: str, *, mode: int = 0o600) -> None:
    fd, temp = tempfile.mkstemp(prefix=f'.{path.name}.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(temp, mode)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def backup(path: Path) -> Path:
    tag = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    candidate = path.with_name(path.name + '.backup.' + tag)
    counter = 0
    while candidate.exists():
        counter += 1
        candidate = path.with_name(path.name + '.backup.' + tag + '.' + str(counter))
    shutil.copy2(path, candidate)
    os.chmod(candidate, 0o600)
    return candidate


def install_template(source: Path, target: Path, replace: bool) -> str:
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise ValueError(f'Refusing unexpected target: {target}')
    if target.exists():
        if target.read_bytes() == source.read_bytes():
            return 'unchanged'
        if not replace:
            return 'preserved existing'
        backup(target)
    atomic_write(target, source.read_text(encoding='utf-8'), mode=0o600)
    return 'updated (backup saved)' if replace else 'installed'


def install_workflow(home: Path) -> bool:
    config = home / 'config.yaml'
    if config.is_symlink() or (config.exists() and not config.is_file()):
        raise ValueError('Refusing unexpected config.yaml target')
    yaml = YAML(typ='rt')
    yaml.preserve_quotes = True
    if config.exists() and config.stat().st_size:
        document = yaml.load(config.read_text(encoding='utf-8'))
    else:
        document = CommentedMap()
    if document is None:
        document = CommentedMap()
    if not isinstance(document, dict):
        raise ValueError('config.yaml must contain a YAML mapping')
    if 'agent' not in document or document['agent'] is None:
        document['agent'] = CommentedMap()
    if not isinstance(document['agent'], dict):
        raise ValueError('config.yaml: agent must be a YAML mapping')
    agent = document['agent']
    previous = agent.get('system_prompt', '')
    if previous is None:
        previous = ''
    if not isinstance(previous, str):
        raise ValueError('config.yaml: agent.system_prompt must be a string')
    if (previous.count(BEGIN) != previous.count(END) or previous.count(BEGIN) > 1
            or (BEGIN in previous and previous.index(BEGIN) > previous.index(END))):
        raise ValueError('Incomplete or repeated managed markers in agent.system_prompt')
    external = previous
    if BEGIN in previous:
        external = re.sub(re.escape(BEGIN) + r'.*?' + re.escape(END), '', previous, flags=re.DOTALL)
    new_block = BEGIN + '\n' + (home / 'WORKFLOW.md').read_text(encoding='utf-8').strip() + '\n' + END
    updated = external.strip()
    updated = (updated + '\n\n' + new_block) if updated else new_block
    if previous == updated:
        return False
    agent['system_prompt'] = LiteralScalarString(updated)
    out = io.StringIO()
    yaml.dump(document, out)
    if config.exists():
        backup(config)
    atomic_write(config, out.getvalue(), mode=0o600)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, required=True, help='Host path to the persistent Hermes state directory')
    parser.add_argument('--repo', type=Path, required=True, help='Portable installer repository root')
    parser.add_argument('--replace-soul', action='store_true', help='Back up and replace an existing SOUL.md')
    parser.add_argument('--replace-workflow', action='store_true', help='Back up and replace an existing WORKFLOW.md')
    args = parser.parse_args()
    home = args.home
    if home.is_symlink() or not home.is_dir():
        raise ValueError('Hermes state directory must exist and must not be a symlink')
    for name, replace in [('SOUL.md', args.replace_soul), ('WORKFLOW.md', args.replace_workflow)]:
        result = install_template(args.repo / 'agent' / name, home / name, replace)
        print(f'{name}: {result}')
    changed = install_workflow(home)
    print('config.yaml: workflow synchronized' if changed else 'config.yaml: already up to date')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        raise SystemExit(1)
