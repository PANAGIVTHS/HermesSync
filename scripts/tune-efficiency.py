#!/usr/bin/env python3
"""Apply a conservative Hermes context-efficiency profile to a local config.yaml.

Read-only by default. Specify --apply to make an atomic, backed-up edit.
Preserves model/provider, reasoning level, credentials, existing prompt and
unrelated configuration. No Docker or network access is required.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import io
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

# Verified against Hermes Agent fd905d2926933a74a1f33ca6c8598992ea757ccb.
SETTINGS = (
    ("agent", "max_turns", 90),
    ("skills", "creation_nudge_interval", 0),
    ("tool_output", "max_bytes", 16000),
    ("tool_output", "max_lines", 400),
    ("tool_output", "max_line_length", 1000),
    ("file_read_max_chars", None, 50000),
    ("compression", "enabled", True),
    ("compression", "threshold_tokens", 120000),
    ("compression", "proactive_prune_tokens", 48000),
    ("compression", "proactive_prune_min_result_chars", 8000),
    ("compression", "proactive_prune_min_reclaim_tokens", 4096),
)


def tune(path: Path, apply: bool) -> int:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Refusing missing, symlinked or non-file config: {path}")
    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    original = path.read_text(encoding="utf-8")
    document = yaml.load(original)
    if not isinstance(document, dict):
        raise ValueError("Expected a YAML mapping in config.yaml")

    changes = []
    for parent, key, value in SETTINGS:
        if key is None:
            old = document.get(parent, "<unset>")
            if old != value or isinstance(old, bool) and not isinstance(value, bool):
                document[parent] = value
                changes.append((parent, old, value))
            continue
        section = document.get(parent)
        if section is None:
            section = CommentedMap()
            document[parent] = section
        if not isinstance(section, dict):
            raise ValueError(f"Expected {parent} to be a YAML mapping; no changes made")
        old = section.get(key, "<unset>")
        if old != value or (isinstance(old, bool) != isinstance(value, bool)):
            section[key] = value
            changes.append((f"{parent}.{key}", old, value))

    if not changes:
        print("No changes: efficiency profile already applied.")
        return 0
    for name, old, new in changes:
        print(f"{name}: {old!r} -> {new!r}")
    if not apply:
        print("DRY RUN. Add --apply to save changes (with backup).")
        return 0

    out = io.StringIO()
    yaml.dump(document, out)
    tag = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_name(f"{path.name}.backup.efficiency.{tag}")
    n = 1
    while backup.exists():
        backup = path.with_name(f"{path.name}.backup.efficiency.{tag}.{n}")
        n += 1
    shutil.copy2(path, backup)
    os.chmod(backup, 0o600)
    fd, temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(out.getvalue())
            f.flush()
            os.fsync(f.fileno())
        os.chmod(temp, stat.S_IMODE(path.stat().st_mode) & 0o600 or 0o600)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    print(f"Applied. Previous config saved as: {backup}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, required=True, help="Hermes persistent .hermes directory")
    parser.add_argument("--apply", action="store_true", help="Write new config, backing up previous config")
    args = parser.parse_args()
    if args.home.is_symlink():
        raise ValueError("Refusing symlinked Hermes home")
    return tune(args.home / "config.yaml", args.apply)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
