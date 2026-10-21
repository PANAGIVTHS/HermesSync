#!/usr/bin/env python3
"""Checkpoint-based, independent Git-backed workspace synchronization.

The original project's .git is never passed to Git or copied to the agent.
Requires Git >= 2.38 (merge-tree --write-tree).
"""
from __future__ import annotations

import argparse
from collections import Counter
import fcntl
import fnmatch
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.syntax import Syntax

# Presentation only: Rich is the same terminal rendering library used by the
# ECE338 GPGPU CLI. All Git, snapshot and synchronization code stays unchanged.
_NO_COLOR = "NO_COLOR" in os.environ
_CONSOLE_OPTIONS = {"highlight": False, "force_terminal": False} if _NO_COLOR else {"highlight": False}
_STDOUT = Console(**_CONSOLE_OPTIONS)
_STDERR = Console(stderr=True, **_CONSOLE_OPTIONS)


def _write(*parts: tuple[object, str], err: bool = False) -> None:
    text = Text()
    for value, style in parts:
        text.append(str(value), style=style)
    (_STDERR if err else _STDOUT).print(text)


def _render_help(parser: argparse.ArgumentParser) -> None:
    """Typer/Rich-like help rendering; argparse still owns all parsing rules."""
    if any(isinstance(a, argparse._SubParsersAction) for a in parser._actions):
        usage = "hermes <command> [options]"
    else:
        usage = parser.format_usage().strip().removeprefix("usage: ")
    _STDOUT.print(Text("Usage", style="bold cyan"), Text("  " + usage))
    if parser.description:
        _STDOUT.print("\n" + parser.description)

    for action in parser._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        commands = Table(box=None, show_header=True, header_style="bold", padding=(0, 2), expand=False)
        commands.add_column("Command", style="bold bright_green", no_wrap=True)
        commands.add_column("Description")
        for choice in action._choices_actions:
            commands.add_row(choice.dest, choice.help or "")
        _STDOUT.print(Panel(commands, title="Commands", border_style="dim", expand=False))

    args = [a for a in parser._actions if not a.option_strings and
            not isinstance(a, argparse._SubParsersAction)]
    if args:
        arguments = Table(box=None, header_style="bold", padding=(0, 2), expand=False)
        arguments.add_column("Argument", style="bold bright_green")
        arguments.add_column("Description")
        for a in args:
            arguments.add_row(a.metavar or a.dest.upper(), a.help or "")
        _STDOUT.print(Panel(arguments, title="Arguments", border_style="dim", expand=False))

    opts = [a for a in parser._actions if a.option_strings and
            not isinstance(a, argparse._SubParsersAction)]
    if opts:
        options = Table(box=None, header_style="bold", padding=(0, 2), expand=False)
        options.add_column("Option", style="bold cyan", no_wrap=True)
        options.add_column("Description")
        for a in opts:
            flags = ", ".join(a.option_strings)
            if a.nargs != 0:
                flags += f" {a.metavar or a.dest.upper()}"
            options.add_row(flags, a.help or "")
        _STDOUT.print(Panel(options, title="Options", border_style="dim", expand=False))
    _STDOUT.print()


class RichArgumentParser(argparse.ArgumentParser):
    def print_help(self, file=None):
        # Preserve conventional plain text when caller passes an explicit file.
        if file is not None:
            return super().print_help(file)
        _render_help(self)

    def error(self, message: str) -> None:
        _write(("Error: ", "bold red"), (message, ""), err=True)
        _STDERR.print(Text(self.format_usage().strip(), style="dim"))
        self.exit(2)


class RichVersionAction(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        _write(("Hermes Sync", "bold bright_green"), (" v9", ""))
        parser.exit(0)

BASE = Path(os.environ.get("HERMES_INSTALL_DIR", str(Path.home() / "Hermes"))).expanduser().resolve()
WORKSPACES = BASE / "workspaces"
STATE_ROOT = BASE / "sync" / "projects"

# Never include VCS metadata, private credentials, or enormous dependency directories.
# These exclusions are explicit and intentionally do not consult the source repo's Git.
OMIT_DIRS = {".git", ".hg", ".svn", "node_modules", ".venv", "__pycache__"}
OMIT_FILES = {".env", ".DS_Store", "id_rsa", "id_ed25519"}
OMIT_SUFFIXES = (".pem", ".p12", ".pfx", ".key")

class SyncError(Exception):
    pass


# Terminal presentation: GPGPU's Rich table/status conventions.
# Kept separate from repository state and merge calculations.
def label(name: str, value: str) -> None:
    _write((f"  {name + ':':<18}", "dim"), (value, "cyan" if name in ("Source", "Desktop", "Agent", "Hermes", "Location", "Agent files", "Sync history") else ""))


def notice_error(message: str, kind: str = "red") -> None:
    glyph = "!" if kind == "yellow" else "✗"
    _write((glyph + " ", "bold " + kind), (message, ""), err=True)


def notice(message: str, kind: str = "green") -> None:
    colors = {"green": "bold green", "yellow": "bold yellow", "red": "bold red", "cyan": "bold cyan"}
    symbols = {"green": "✓", "yellow": "!", "red": "✗", "cyan": "→"}
    _write((symbols[kind] + " ", colors[kind]), (message, ""))


def info(message: str) -> None:
    _write(("  " + message, "dim"))


def heading(name: str) -> None:
    _STDOUT.print(Text(name, style="bold cyan"))


def _file_row(kind: str, rel: str) -> None:
    role = {"A": "green", "M": "yellow", "D": "red"}.get(kind, "cyan")
    _write(("    " + kind, f"bold {role}"), ("  " + rel, ""))


def changed_paths(repo: Path, left: str, right: str) -> list[tuple[str, str]]:
    """Name/status pairs with NUL delimiting; renames shown as add + delete."""
    data = git(repo, "diff", "--no-renames", "--name-status", "-z", left, right).stdout
    fields = data.split(b"\0")
    return [(fields[i].decode("ascii", "replace"), os.fsdecode(fields[i + 1]))
            for i in range(0, len(fields) - 1, 2)]


def show_changes(repo: Path, left: str, right: str, title: str, limit: int = 8) -> int:
    changes = changed_paths(repo, left, right)
    counts = Counter(s[0] for s, _ in changes)
    line = Text("  " + title + ": ", style="bold")
    if not changes:
        line.append("no changes", style="dim")
    else:
        for kind, word, style in (("A", "added", "green"), ("M", "modified", "yellow"), ("D", "deleted", "red")):
            if counts[kind]:
                if len(line.plain) > len("  " + title + ": "):
                    line.append("  ")
                line.append(f"{counts[kind]} {word}", style=style)
    _STDOUT.print(line)
    for kind, rel in changes[:limit]:
        _file_row(kind, rel)
    if len(changes) > limit:
        info(f"... {len(changes) - limit} more files (hermes status --verbose for all)")
    return len(changes)


def pattern_excluded(rel: str, is_dir: bool, patterns: list[str]) -> bool:
    """Small, explicit glob syntax, NOT .gitignore syntax.

    Pattern containing / matches the full relative path; trailing / excludes
    a directory and its entire subtree. Pattern without / matches a basename
    at any depth. Wildcards match via fnmatch (including path separators).
    """
    parts = rel.split("/")
    for pat in patterns:
        if pat.endswith("/"):
            base = pat.rstrip("/")
            if not base:
                continue
            if "/" in base:
                if rel == base or rel.startswith(base + "/"):
                    return True
            elif base in parts[:len(parts) if is_dir else -1]:
                return True
        elif "/" in pat:
            if fnmatch.fnmatchcase(rel, pat):
                return True
        elif any(fnmatch.fnmatchcase(part, pat) for part in parts):
            return True
    return False


def excluded(name: str, is_dir: bool) -> bool:
    if name == ".git":
        return True
    if is_dir and name in OMIT_DIRS:
        return True
    if name in OMIT_FILES or name.startswith(".env."):
        return True
    if name.lower().endswith(OMIT_SUFFIXES):
        return True
    return False


# Git-standard ignore matching is evaluated in a PRIVATE scratch directory.
# Never consult or modify the user's original .git or index. A shared proposed
# .gitignore view is used on both sides so a newly-added agent .gitignore can
# suppress untracked build output even if the desktop lacks that file.
_ACTIVE_IGNORE = None


def _ignore_files(root: Path) -> dict[str, bytes]:
    """Read only .gitignore files, never anything from an original .git."""
    found = {}
    if not root.is_dir():
        return found
    for cur, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if not excluded(d, True) and
                   not (Path(cur) / d).is_symlink()]
        if ".gitignore" in files and not (Path(cur) / ".gitignore").is_symlink():
            file = Path(cur) / ".gitignore"
            found[file.relative_to(root).as_posix()] = file.read_bytes()
    return found


def _merge_ignore_bytes(base: bytes | None, left: bytes | None, right: bytes | None) -> bytes | None:
    """Three-way merge a single ignore file; defer ambiguous cases to the real merge."""
    if left == right:
        return left
    if left == base:
        return right
    if right == base:
        return left
    if base is None or left is None or right is None:
        return base
    with tempfile.TemporaryDirectory(prefix="hermes-ignore-merge-") as temp:
        f = [Path(temp) / x for x in ("ours", "ancestor", "theirs")]
        for path, data in zip(f, (left, base, right)):
            path.write_bytes(data)
        p = subprocess.run(["git", "merge-file", "-p", *map(str, f)], capture_output=True)
        return p.stdout if p.returncode == 0 else base


class IgnorePolicy:
    """Match ignore rules in a private scratch Git tree, independent of source Git."""
    def __init__(self, source: Path, agent: Path | None = None, state: dict | None = None):
        self.temp = tempfile.TemporaryDirectory(prefix="hermes-ignore-")
        self.folder = Path(self.temp.name)
        self.tracked: set[str] = set()
        if state:
            repo = Path(state["repo"])
            for rev in {state["base"], state.get("desktop_head", state["base"]),
                        state.get("agent_head", state["base"])}:
                tree = out(repo, "rev-parse", f"{rev}^{{tree}}")
                self.tracked.update(tree_files(repo, tree))
        source_rules = _ignore_files(source)
        if agent is None:
            resolved = source_rules
        else:
            agent_rules = _ignore_files(agent)
            base_rules = {}
            if state:
                repo = Path(state["repo"])
                base_tree = out(repo, "rev-parse", f"{state['base']}^{{tree}}")
                for rel, (_mode, oid) in tree_files(repo, base_tree).items():
                    if Path(rel).name == ".gitignore":
                        base_rules[rel] = git(repo, "cat-file", "blob", oid).stdout
            resolved = {}
            for rel in sorted(source_rules.keys() | agent_rules.keys() | base_rules.keys()):
                content = _merge_ignore_bytes(base_rules.get(rel), source_rules.get(rel), agent_rules.get(rel))
                if content is not None:
                    resolved[rel] = content
        for rel, data in resolved.items():
            path = self.folder / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        init = subprocess.run(["git", "init", "-q", str(self.folder)], capture_output=True)
        if init.returncode:
            self.temp.cleanup()
            raise SyncError("Cannot initialize temporary .gitignore matcher: " + init.stderr.decode(errors="replace"))

    def close(self):
        self.temp.cleanup()

    def match(self, paths: list[str]) -> set[str]:
        if not paths:
            return set()
        data = b"\0".join(os.fsencode(name) for name in paths) + b"\0"
        env = dict(os.environ)
        env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull})
        p = subprocess.run(["git", "-C", str(self.folder), "-c", f"core.excludesfile={os.devnull}",
                            "check-ignore", "--no-index", "--stdin", "-z"],
                           input=data, capture_output=True, env=env)
        if p.returncode not in (0, 1):
            raise SyncError("Git .gitignore evaluation failed: " + p.stderr.decode(errors="replace"))
        ignored = {os.fsdecode(x) for x in p.stdout.split(b"\0") if x}
        # Git's rule: .gitignore only applies to untracked files. Keep anything
        # from already-accepted snapshots under management to avoid deletions.
        return {rel for rel in ignored if rel not in self.tracked and
                Path(rel).name != ".gitignore"}


@contextmanager
def ignore_scope(source: Path, agent: Path | None = None, state: dict | None = None):
    global _ACTIVE_IGNORE
    previous = _ACTIVE_IGNORE
    policy = IgnorePolicy(source, agent, state)
    _ACTIVE_IGNORE = policy
    try:
        yield
    finally:
        _ACTIVE_IGNORE = previous
        policy.close()


def paths(root: Path, patterns: list[str] | None = None):
    patterns = patterns or []
    if not root.is_dir() or root.is_symlink():
        raise SyncError(f"Not a real directory: {root}")
    candidates = []
    for cur, dirs, files in os.walk(root, topdown=True, followlinks=False):
        cur_p = Path(cur)
        # Skip ignored directories early: Gradle/Node caches can contain many
        # thousands of files (and symlinks). Previously snapshotted files are
        # not pruned, even if a new rule now ignores their parent directory.
        ignored_dirs = set()
        if _ACTIVE_IGNORE:
            eligible_dirs = [name for name in dirs if not excluded(name, True) and
                             not pattern_excluded((cur_p / name).relative_to(root).as_posix(), True, patterns)]
            ignored_dirs = _ACTIVE_IGNORE.match([
                (cur_p / name).relative_to(root).as_posix() + "/" for name in eligible_dirs
            ])
        for name in list(dirs):
            rel = (cur_p / name).relative_to(root).as_posix()
            if excluded(name, True) or pattern_excluded(rel, True, patterns):
                dirs.remove(name)
            elif rel + "/" in ignored_dirs and not any(
                tracked.startswith(rel + "/") for tracked in _ACTIVE_IGNORE.tracked
            ):
                dirs.remove(name)
            elif (cur_p / name).is_symlink():
                raise SyncError(f"Symlink directory not supported: {cur_p / name}")
        for name in files:
            if excluded(name, False) or pattern_excluded((cur_p / name).relative_to(root).as_posix(), False, patterns):
                continue
            p = cur_p / name
            relative = p.relative_to(root).as_posix()
            if any(ord(c) < 32 or ord(c) == 127 for c in relative):
                raise SyncError(f"Control characters in filename are unsupported: {relative!r}")
            candidates.append((relative, p))
    ignored = _ACTIVE_IGNORE.match([rel for rel, _ in candidates]) if _ACTIVE_IGNORE else set()
    for relative, p in candidates:
        if relative in ignored:
            continue
        s = p.lstat()
        if not stat.S_ISREG(s.st_mode):
            raise SyncError(f"Non-regular file not supported: {p}")
        yield relative, p, ("100755" if s.st_mode & stat.S_IXUSR else "100644")


def git(repo: Path, *args: str, input_bytes: bytes | None = None, check=True) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.update({
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_AUTHOR_NAME": "Hermes Sync", "GIT_AUTHOR_EMAIL": "hermes sync@localhost",
        "GIT_COMMITTER_NAME": "Hermes Sync", "GIT_COMMITTER_EMAIL": "hermes sync@localhost",
        "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "1",
    })
    # Explicit Git directory: this command never discovers or uses the original .git.
    cmd = ["git", f"--git-dir={repo}", *args]
    p = subprocess.run(cmd, input=input_bytes, capture_output=True, env=env)
    if check and p.returncode:
        raise SyncError(f"Git failed ({' '.join(args)}):\n{p.stderr.decode(errors='replace')}\n{p.stdout.decode(errors='replace')}")
    return p


def out(repo, *args) -> str:
    return git(repo, *args).stdout.decode().strip()


def snapshot(repo: Path, folder: Path, parent: str | None, label: str, patterns: list[str] | None = None) -> tuple[str, str, int]:
    """Store exact file bytes as Git blobs, without Git filters or the source index."""
    entries = []
    count = 0
    for name, file, mode in paths(folder, patterns):
        data = file.read_bytes()
        oid = git(repo, "hash-object", "-w", "--stdin", input_bytes=data).stdout.decode().strip()
        entries.append((name, mode, oid))
        count += 1
    with tempfile.TemporaryDirectory(prefix="hermes-index-") as t:
        idx = str(Path(t) / "index")
        # GIT_INDEX_FILE only ever points to a temporary index, never the project's index.
        env = dict(os.environ)
        env.update({
            "GIT_INDEX_FILE": idx, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_AUTHOR_NAME": "Hermes Sync", "GIT_AUTHOR_EMAIL": "hermes sync@localhost",
            "GIT_COMMITTER_NAME": "Hermes Sync", "GIT_COMMITTER_EMAIL": "hermes sync@localhost",
        })
        def g(*args, data=None):
            p = subprocess.run(["git", f"--git-dir={repo}", *args], input=data, capture_output=True, env=env)
            if p.returncode:
                raise SyncError(p.stderr.decode(errors="replace"))
            return p.stdout.decode().strip()
        g("read-tree", "--empty")
        # `--index-info` inputs mode/oid/stage and filename, NUL-delimited.
        payload = b"".join(f"{mode} {oid} 0\t".encode() + os.fsencode(name) + b"\0"
                           for name, mode, oid in entries)
        if payload:
            g("update-index", "-z", "--index-info", data=payload)
        tree = g("write-tree")
    commit_args = ["commit-tree", tree]
    if parent:
        commit_args.extend(["-p", parent])
    commit_args.extend(["-m", label])
    commit = out(repo, *commit_args)
    return commit, tree, count


def tree_files(repo: Path, tree: str) -> dict[str, tuple[str, str]]:
    listing = git(repo, "ls-tree", "-r", "-z", tree).stdout
    result = {}
    for row in listing.split(b"\0"):
        if not row:
            continue
        meta, name = row.split(b"\t", 1)
        mode, typ, oid = meta.decode().split(" ")
        if typ != "blob" or mode not in {"100644", "100755"}:
            raise SyncError("Unsupported Git tree entry. Only regular files allowed.")
        path = os.fsdecode(name)
        if path.startswith("/") or ".." in Path(path).parts or any(excluded(part, False) or part == ".git" for part in Path(path).parts):
            raise SyncError(f"Unsafe Git tree path: {path}")
        result[path] = (mode, oid)
    return result


def ensure_path_safe(root: Path, rel: str):
    # Deny symlink traversal or links at destination even if another process added them.
    p = root
    for comp in Path(rel).parts:
        p = p / comp
        if p.is_symlink():
            raise SyncError(f"Symlink at destination: {p}")
    return p


def apply_tree(repo: Path, root: Path, before_tree: str, after_tree: str) -> int:
    """Change only managed paths, leave .git/excluded files untouched."""
    old = tree_files(repo, before_tree)
    new = tree_files(repo, after_tree)
    changes = [rel for rel in sorted(set(old) | set(new)) if old.get(rel) != new.get(rel)]
    for rel in changes:
        p = ensure_path_safe(root, rel)
        if rel in new and p.exists() and not (p.is_file() or p.is_dir()):
            raise SyncError(f"Cannot replace non-file path: {p}")
    # Deletions first (handles file -> dir and renames).
    for rel in changes:
        if rel not in new:
            p = ensure_path_safe(root, rel)
            if p.is_file():
                p.unlink()
    # Clear empty directories *before* creating replacement files. This also
    # handles a/b.txt -> a (file) without modifying unrelated paths.
    for rel in sorted(changes, key=lambda x: len(Path(x).parts), reverse=True):
        parent = (root / rel).parent
        while parent != root and parent.is_relative_to(root):
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent
    for rel in changes:
        if rel not in new:
            continue
        p = ensure_path_safe(root, rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        mode, oid = new[rel]
        data = git(repo, "cat-file", "blob", oid).stdout
        fd, temp = tempfile.mkstemp(prefix=".hermes-write-", dir=str(p.parent))
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(temp, 0o755 if mode == "100755" else 0o644)
            os.replace(temp, p)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
    return len(changes)


def config_path(project: str) -> Path:
    return STATE_ROOT / project / "state.json"


def load_project(cwd: Path, name: str | None):
    if not STATE_ROOT.is_dir():
        raise SyncError("No registered projects. Run hermes share first.")
    if name:
        file = config_path(name)
        if not file.exists():
            raise SyncError(f"Unknown project: {name}")
        return file, json.loads(file.read_text())
    for file in STATE_ROOT.glob("*/state.json"):
        state = json.loads(file.read_text())
        if Path(state["source"]) == cwd.resolve():
            return file, state
    raise SyncError(f"Project not registered: {cwd}. Run hermes share here first, or use --project NAME.")


def save(file: Path, state: dict):
    temp = file.with_name("state.json.tmp")
    temp.write_text(json.dumps(state, indent=2) + "\n")
    os.replace(temp, file)


@contextmanager
def locked(file: Path):
    lock = file.parent / ".lock"
    with open(lock, "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        yield


def get_worktrees(state: dict):
    source, agent = Path(state["source"]), Path(state["agent"])
    if not source.is_dir() or not agent.is_dir():
        raise SyncError("A registered workspace is missing")
    if source.resolve() == agent.resolve():
        raise SyncError("Workspaces must be different")
    return source, agent


def current_snapshots(state: dict) -> dict:
    repo = Path(state["repo"])
    src, agent = get_worktrees(state)
    patterns = state.get("exclude", [])
    # Preserve ancestry across PUSH: future merges know what desktop edits the
    # agent has already incorporated, preventing duplicate/false conflicts.
    sc, st, ns = snapshot(repo, src, state.get("desktop_head", state["base"]), "Desktop checkpoint", patterns)
    ac, at, na = snapshot(repo, agent, state.get("agent_head", state["base"]), "Hermes checkpoint", patterns)
    merged = git(repo, "merge-tree", "--write-tree", "--messages", sc, ac, check=False)
    output = merged.stdout.decode(errors="replace")
    tree = output.splitlines()[0].strip() if output else ""
    if not re.fullmatch(r"[0-9a-f]{40,64}", tree) or merged.returncode not in (0, 1):
        raise SyncError("Could not compute merge-tree:\n" + output[:5000] + merged.stderr.decode(errors="replace")[:2000])
    paths_in_conflict = sorted(set(re.findall(r"(?m)^(?:100644|100755|120000) [0-9a-f]{40,64} [123]\t([^\n]+)$", output)))
    return {"desktop": st, "agent": at, "merged": tree, "desktop_commit": sc, "agent_commit": ac,
            "files_desktop": ns, "files_agent": na, "conflict": merged.returncode == 1,
            "conflicts": paths_in_conflict, "messages": output[:8000]}


def sample(state: dict) -> dict:
    preview = current_snapshots(state)
    if preview["conflict"]:
        resolution = state.get("resolution")
        valid = (resolution and resolution.get("status") == "ready"
                 and resolution.get("base") == state["base"]
                 and resolution.get("desktop") == preview["desktop"]
                 and resolution.get("agent") == preview["agent"])
        if valid:
            preview["merged"] = resolution["merged"]
            preview["manual"] = True
        else:
            affected = ", ".join(preview["conflicts"][:5]) or "one or more files"
            rest = f" (+{len(preview['conflicts'])-5} more)" if len(preview["conflicts"]) > 5 else ""
            raise SyncError(f"Merge conflict: {affected}{rest}. No files changed.\n"
                            "Use hermes review --gui to resolve visually, or hermes resolve.")
    return preview


def clear_resolution(file: Path, state: dict):
    state["resolution"] = None
    shutil.rmtree(file.parent / "resolution", ignore_errors=True)


def do_resolve(file: Path, state: dict, args):
    repo = Path(state["repo"])
    if args.abort:
        clear_resolution(file, state)
        state["review"] = None
        save(file, state)
        notice("Conflict resolution discarded. Workspaces unchanged.")
        return
    snapshot_now = current_snapshots(state)
    scratch = file.parent / "resolution"
    working = scratch / "merged"
    record = state.get("resolution")
    if args.continue_resolution:
        if not record or record.get("status") != "editing":
            raise SyncError("No active resolution to complete. Run hermes resolve first.")
        if (not snapshot_now["conflict"] or record["base"] != state["base"]
                or record["desktop"] != snapshot_now["desktop"]
                or record["agent"] != snapshot_now["agent"]):
            raise SyncError("Source or Hermes files changed since resolution began. Run hermes resolve --abort and start over.")
        if not working.is_dir() or working.is_symlink():
            raise SyncError("Resolution workspace missing or invalid.")
        for rel in record["conflicts"]:
            p = working / rel
            if p.is_file() and not p.is_symlink():
                data = p.read_bytes()
                if any(line.startswith((b"<<<<<<< ", b"=======", b">>>>>>> ", b"\\\\ No newline"))
                       for line in data.splitlines()):
                    raise SyncError(f"Unresolved conflict marker in {rel}.")
        commit, tree, _ = snapshot(repo, working, state["base"], "User-resolved merge", state.get("exclude", []))
        # Ensure exclusions or stray files have not unexpectedly appeared.
        tree_files(repo, tree)
        record.update({"status": "ready", "merged": tree})
        state["resolution"] = record
        state["review"] = {k: v for k, v in {**snapshot_now, "merged": tree}.items() if k in ("desktop", "agent", "merged")}
        save(file, state)
        notice("Resolution saved. Run hermes sync to apply it.")
        return
    if record:
        raise SyncError("A resolution already exists. Use --continue or --abort.")
    if not snapshot_now["conflict"]:
        notice("No conflicts to resolve.", "cyan")
        return
    scratch.mkdir(parents=True, exist_ok=False)
    working.mkdir()
    try:
        empty_tree = git(repo, "mktree", input_bytes=b"").stdout.decode().strip()
        apply_tree(repo, working, empty_tree, snapshot_now["merged"])
        report_path = scratch / "CONFLICTS.txt"
        report_path.write_text("Conflicts to resolve in merged/:\n" + "\n".join(snapshot_now["conflicts"])
                               + "\n\nChanges here will not touch desktop or agent files until hermes sync.\n")
        state["resolution"] = {"status": "editing", "base": state["base"],
                               "desktop": snapshot_now["desktop"], "agent": snapshot_now["agent"],
                               "conflicts": snapshot_now["conflicts"]}
        state["review"] = None
        save(file, state)
    except BaseException:
        shutil.rmtree(scratch, ignore_errors=True)
        raise
    notice("Conflict workspace prepared.", "yellow")
    label("Location", str(working))
    if snapshot_now["conflicts"]:
        for rel in snapshot_now["conflicts"][:8]:
            _file_row("!", rel)
    else:
        info("Inspect CONFLICTS.txt; some conflicts have no text markers.")
    info("Edit files, then: hermes resolve --continue")


def report(repo: Path, preview: dict, *, verbose: bool = False):
    """Only show affected file counts by default; opt-in full review is in VS Code."""
    heading("Changes to apply")
    limit = 10_000 if verbose else 6
    show_changes(repo, preview["desktop"], preview["merged"], "Desktop", limit)
    show_changes(repo, preview["agent"], preview["merged"], "Hermes", limit)


def do_share(args):
    source = Path(args.path).expanduser().resolve()
    if not source.is_dir():
        raise SyncError(f"Not a directory: {source}")
    if source == BASE or source.is_relative_to(BASE) or BASE.is_relative_to(source):
        raise SyncError("Refusing to use a directory inside ~/Hermes as the original project. Use a separate disposable source directory.")
    name = args.name or source.name
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,80}", name) or name in {".", ".."}:
        raise SyncError("Invalid project name. Use letters, digits, underscore, dot, hyphen.")
    agent = WORKSPACES / name
    project_state = STATE_ROOT / name
    if project_state.exists():
        raise SyncError(f"Project already registered: {name}")
    if agent.exists():
        raise SyncError(f"Agent directory already exists (refusing overwrite): {agent}")
    patterns = list(args.exclude or [])
    for pat in patterns:
        if not pat or pat.startswith("!") or "\x00" in pat:
            raise SyncError(f"Unsupported --exclude pattern: {pat!r}")
    with ignore_scope(source):
        listing = list(paths(source, patterns))
    total_bytes = sum(p.stat().st_size for _, p, _ in listing)
    heading(f"Share · {name}")
    label("Source", str(source))
    label("Hermes", f"/workspace/{name}")
    label("Files", f"{len(listing)} ({total_bytes / (1024*1024):.1f} MiB)")
    if patterns:
        label("Extra excludes", ", ".join(patterns))
    if listing:
        shown = listing if args.verbose else listing[:12]
        for rel, _, _ in shown:
            _write(("    " + rel, ""))
        if len(listing) > len(shown):
            info(f"... {len(listing) - len(shown)} more files (--verbose shows all)")
    notice("Original .git is excluded; Git-standard .gitignore rules are applied. Check for secrets.", "yellow")
    if not args.yes:
        notice("DRY RUN — nothing copied. Add --yes to share.", "cyan")
        return
    WORKSPACES.mkdir(parents=True, exist_ok=True)
    project_state.mkdir(parents=True)
    repo = project_state / "repo.git"
    agent.mkdir()
    try:
        init = subprocess.run(["git", "init", "--bare", "--quiet", str(repo)], capture_output=True)
        if init.returncode:
            raise SyncError(init.stderr.decode(errors="replace"))
        with ignore_scope(source):
            base, tree, _ = snapshot(repo, source, None, "Initial common snapshot", patterns)
        git(repo, "update-ref", "refs/hermes/base", base)
        # An empty initial destination is populated directly from the first snapshot.
        empty_tree = git(repo, "mktree", input_bytes=b"").stdout.decode().strip()
        apply_tree(repo, agent, empty_tree, tree)
        state = {"version": 2, "name": name, "source": str(source), "agent": str(agent), "repo": str(repo), "base": base, "desktop_head": base, "agent_head": base, "exclude": patterns,
                 "review": None, "resolution": None, "undo": None}
        save(config_path(name), state)
    except BaseException:
        shutil.rmtree(agent, ignore_errors=True)
        shutil.rmtree(project_state, ignore_errors=True)
        raise
    notice(f"Shared {len(listing)} files → /workspace/{name}")



def do_list(_args=None):
    """List sync registrations, including orphaned projects (desktop folder removed)."""
    if not STATE_ROOT.is_dir():
        notice("No registered projects.", "cyan")
        return
    records = sorted(STATE_ROOT.glob("*/state.json"), key=lambda p: p.parent.name.lower())
    if not records:
        notice("No registered projects.", "cyan")
        return
    heading(f"Projects · {len(records)}")
    table = Table(box=None, show_header=True, header_style="bold", padding=(0, 2))
    table.add_column("Project", style="bold bright_green")
    table.add_column("Desktop")
    table.add_column("Hermes workspace", style="cyan")
    table.add_column("Status")
    active = _current_active() if (BASE / ".env").is_file() else _IDLE_NAME
    for file in records:
        try:
            state = json.loads(file.read_text())
            name = state.get("name", file.parent.name)
            source = Path(state["source"])
            agent = Path(state["agent"])
            flags = []
            if name == active:
                flags.append("active")
            if not source.is_dir():
                flags.append("desktop missing")
            if not agent.is_dir():
                flags.append("workspace missing")
            table.add_row(name, str(source), f"/workspace/{name}",
                          Text(", ".join(flags) if flags else "ready", style="yellow" if flags else "green"))
        except (OSError, ValueError, KeyError) as exc:
            table.add_row(file.parent.name, "—", "—", Text(f"invalid registration: {exc}", style="red"))
    _STDOUT.print(table)


def do_purge(file: Path, state: dict, args):
    """Irreversibly unregister one project and erase *all* its agent files.

    The desktop source is intentionally not inspected, altered, or required to
    exist. All paths are derived from verified registration paths. This does
    not erase webui history or shared Hermes state (outside the project).
    """
    name = state.get("name")
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,80}", name):
        raise SyncError("Invalid registered project name; refusing purge.")
    state_dir = STATE_ROOT / name
    agent_dir = WORKSPACES / name
    if (file != state_dir / "state.json" or file.is_symlink()
            or WORKSPACES.is_symlink() or STATE_ROOT.is_symlink() or STATE_ROOT.parent.is_symlink()
            or state_dir.is_symlink() or agent_dir.is_symlink()
            or Path(state.get("agent", "")) != agent_dir
            or Path(state.get("repo", "")) != state_dir / "repo.git"):
        raise SyncError("Unsafe project paths or registration mismatch; refusing purge.")
    if state.get("pending_operation"):
        raise SyncError("An operation is interrupted. Run hermes recover --project " + name + " before purging.")
    if agent_dir.exists() and not agent_dir.is_dir():
        raise SyncError("Agent workspace is not a normal directory; refusing purge.")
    if not state_dir.is_dir():
        raise SyncError("Sync state is missing; refusing purge.")
    if (BASE / ".env").is_file() and _current_active() == name:
        raise SyncError(f"Project {name} is active. Run hermes use --none before purging.")
    heading(f"Purge · {name}")
    label("Agent files", str(agent_dir))
    label("Sync history", str(state_dir))
    notice("Deletes ALL agent files (including excluded files) and undo history.", "yellow")
    info("Desktop source, original .git, other projects and Docker are untouched.")
    if not args.yes:
        if not sys.stdin.isatty():
            raise SyncError("Confirmation required. Run interactively or use hermes purge --yes.")
        answer = _STDOUT.input(Text(f"Type {name} to permanently purge this project: ", style="bold yellow")).strip()
        if answer != name:
            notice("Cancelled. Nothing deleted.", "cyan")
            return
    # Remove agent first. A failed deletion leaves state.json available so
    # the command can be retried. rmtree does not follow nested symlinks.
    if agent_dir.exists():
        shutil.rmtree(agent_dir)
    shutil.rmtree(state_dir)
    notice(f"Purged {name}. Desktop files were not changed.")


def do_status(state: dict, *, verbose: bool = False):
    repo = Path(state["repo"])
    preview = current_snapshots(state)
    base_tree = out(repo, "rev-parse", f"{state['base']}^{{tree}}")
    heading(f"Project: {state['name']}")
    label("Desktop", state["source"])
    label("Agent", f"/workspace/{state['name']}")
    _STDOUT.print()
    limit = 10_000 if verbose else 8
    show_changes(repo, base_tree, preview["desktop"], "Desktop changes", limit)
    show_changes(repo, base_tree, preview["agent"], "Hermes changes", limit)
    _STDOUT.print()
    status = "CONFLICT" if preview["conflict"] else "clean"
    _write(("  Automatic merge: ", "bold"), (status, "bold red" if preview["conflict"] else "green"))
    if state.get("gui_review"):
        notice("GUI review ready · hermes sync applies its saved edits", "cyan")
    elif state.get("resolution"):
        notice(f"Resolution: {state['resolution']['status']}", "yellow")
    elif preview["conflict"]:
        info("Resolve: hermes review --gui")
    if state.get("undo"):
        info("Undo available: hermes undo (unless files changed)")


def remove_gui_review(file: Path, state: dict):
    """Only remove this project's private GUI scratch tree."""
    state.pop("gui_review", None)
    shutil.rmtree(file.parent / "gui-review", ignore_errors=True)


def gui_git(root: Path, *args):
    """Git exclusively in the scratch repo, never in the desktop project."""
    env = dict(os.environ)
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_AUTHOR_NAME": "Hermes Review", "GIT_AUTHOR_EMAIL": "review@localhost",
                "GIT_COMMITTER_NAME": "Hermes Review", "GIT_COMMITTER_EMAIL": "review@localhost",
                "GIT_TERMINAL_PROMPT": "0"})
    p = subprocess.run(["git", "-C", str(root), "-c", "commit.gpgsign=false", *args],
                       capture_output=True, env=env)
    if p.returncode:
        raise SyncError(f"GUI Git setup failed: {p.stderr.decode(errors='replace')}")
    return p


def launch_gui(folder: Path):
    editor = shutil.which("code") or shutil.which("codium")
    if not editor:
        raise SyncError("VS Code CLI not found (code/codium). Install VS Code or add its command to PATH.")
    subprocess.Popen([editor, "--new-window", str(folder)],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


def do_gui_review(file: Path, state: dict, args):
    """Populate a separate SCM-enabled review project: HEAD=desktop; worktree=merge."""
    current = current_snapshots(state)
    existing = state.get("gui_review")
    review_root = file.parent / "gui-review"
    working = review_root / "working"
    if args.abort_gui:
        remove_gui_review(file, state)
        save(file, state)
        notice("GUI review discarded. Workspaces unchanged.")
        return
    if existing and not args.reset_gui:
        if (existing.get("base") != state["base"] or
                existing.get("desktop") != current["desktop"] or
                existing.get("agent") != current["agent"]):
            raise SyncError("GUI review is stale. Reopen with hermes review --gui --reset-gui to discard it.")
        if not working.is_dir():
            raise SyncError("GUI review folder is missing. Use --gui --reset-gui.")
        launch_gui(working)
        notice("Reopened VS Code review.")
        return
    if args.reset_gui or existing:
        remove_gui_review(file, state)
    repo = Path(state["repo"])
    review_root.mkdir(parents=True, exist_ok=True)
    working.mkdir()
    try:
        empty_tree = git(repo, "mktree", input_bytes=b"").stdout.decode().strip()
        apply_tree(repo, working, empty_tree, current["desktop"])
        gui_git(working, "init", "--quiet")
        gui_git(working, "add", "--all", "--force")
        gui_git(working, "commit", "--quiet", "--allow-empty", "-m", "Desktop before Hermes review")
        apply_tree(repo, working, current["desktop"], current["merged"])
        # Intent-to-add makes new files visible as diffs, even if ignored by a
        # transferred .gitignore. No user project index is touched.
        added = sorted(set(tree_files(repo, current["merged"])) - set(tree_files(repo, current["desktop"])))
        if added:
            gui_git(working, "add", "-N", "--force", "--", *added)
        # Check conflict markers explicitly when auto merge had a conflict.
        markerless = []
        for rel in current["conflicts"]:
            p = working / rel
            if not p.is_file() or p.is_symlink() or not any(
                line.startswith(b"<<<<<<< ") for line in p.read_bytes().splitlines()
            ):
                markerless.append(rel)
        state["gui_review"] = {"base": state["base"], "desktop": current["desktop"],
                               "agent": current["agent"], "conflicts": current["conflicts"],
                               "markerless": markerless,
                               "markerless_initial": {rel: tree_files(repo, current["merged"]).get(rel)
                                                       for rel in markerless}}
        save(file, state)
    except BaseException:
        remove_gui_review(file, state)
        save(file, state)
        raise
    launch_gui(working)
    notice("VS Code review opened.")
    info("Source Control → compare and edit; save when finished.")
    if current["conflict"]:
        notice(f"Resolve conflicts in {len(current['conflicts'])} file(s) before syncing.", "yellow")
        if markerless:
            info("Binary/delete conflicts need an explicit file change/removal:")
            for rel in markerless[:8]:
                _write(("    " + rel, ""))
    info("Apply: hermes sync     Discard: hermes review --abort-gui")


def gui_preview(file: Path, state: dict) -> dict:
    """Use exactly what the user saved in the GUI, guarded by snapshot IDs."""
    record = state.get("gui_review")
    if not record:
        return sample(state)
    current = current_snapshots(state)
    if any((record.get(k) != current.get(k)) for k in ("desktop", "agent")) or record.get("base") != state["base"]:
        raise SyncError("GUI review stale: Desktop or Hermes changed since review. No changes applied. "
                        "Use hermes review --gui --reset-gui to start over (discards edits).")
    working = file.parent / "gui-review" / "working"
    if not working.is_dir() or working.is_symlink() or not (working / ".git").is_dir():
        raise SyncError("GUI review workspace missing or invalid.")
    repo = Path(state["repo"])
    _, result, _ = snapshot(repo, working, state["base"], "GUI-approved merge", state.get("exclude", []))
    resolved = tree_files(repo, result)
    for rel in record.get("conflicts", []):
        p = working / rel
        if rel in resolved:
            data = p.read_bytes()
            if any(line.startswith((b"<<<<<<< ", b"=======", b">>>>>>> ", b"\\ No newline"))
                   for line in data.splitlines()):
                raise SyncError(f"Unresolved conflict markers in GUI review: {rel}")
    for rel in record.get("markerless", []):
        initial = record["markerless_initial"][rel]
        if resolved.get(rel) == (tuple(initial) if initial is not None else None):
            raise SyncError(f"Conflict without markers not explicitly resolved: {rel}. Change/remove it in GUI review.")
    current["merged"] = result
    current["manual"] = True
    current["gui"] = True
    return current


def do_review(file: Path, state: dict, args):
    if args.gui or args.abort_gui:
        do_gui_review(file, state, args)
        return
    repo = Path(state["repo"])
    preview = sample(state)
    report(repo, preview)
    if preview.get("manual"):
        notice("Using saved conflict resolution.", "cyan")
    if args.patch:
        _STDOUT.print(Text("\nDesktop diff (proposed)", style="bold cyan"))
        diff = git(repo, "diff", "--no-ext-diff", preview["desktop"], preview["merged"]).stdout.decode(errors="replace")[:40000]
        _STDOUT.print(Syntax(diff, "diff", theme="ansi_dark", background_color="default", word_wrap=False))
    state["review"] = {key: preview[key] for key in ("desktop", "agent", "merged")}
    save(file, state)
    notice("Review ready. Apply: hermes sync  |  Send only: hermes push")


def do_apply(file: Path, state: dict, direction: str):
    repo = Path(state["repo"])
    source, agent = get_worktrees(state)
    if direction == "push" and state.get("gui_review"):
        raise SyncError("GUI review active. Run hermes sync or hermes review --abort-gui before pushing.")
    preview = gui_preview(file, state) if direction == "sync" else sample(state)
    # Review is optional. Always calculate fresh snapshots from the current files.
    report(repo, preview)
    if preview["desktop"] == preview["agent"] == preview["merged"]:
        # Both sides may have independently reached exactly the same new snapshot.
        # Advance the common base so future merges do not use an outdated ancestor.
        old_tree = out(repo, "rev-parse", f"{state['base']}^{{tree}}")
        if old_tree != preview["merged"]:
            newbase = out(repo, "commit-tree", preview["merged"], "-p", preview["desktop_commit"], "-p", preview["agent_commit"], "-m", "Accepted identical checkpoints")
            git(repo, "update-ref", "refs/hermes/base", newbase)
            state["base"] = newbase
            state["desktop_head"] = newbase
            state["agent_head"] = newbase
            clear_resolution(file, state)
            remove_gui_review(file, state)
            state["undo"] = None
            state["review"] = None
            save(file, state)
            notice("Workspaces already agree. Checkpoint updated.")
        else:
            if state.get("gui_review"):
                remove_gui_review(file, state)
                save(file, state)
            notice("Already up to date.")
        return
    if direction == "push" and preview.get("manual"):
        raise SyncError("Manual resolutions require hermes sync (not hermes push).")
    before_base = state["base"]
    before_heads = {"desktop": state.get("desktop_head", state["base"]),
                    "agent": state.get("agent_head", state["base"])}
    before = {"desktop": preview["desktop"], "agent": preview["agent"]}
    after = {"desktop": preview["merged"] if direction == "sync" else before["desktop"], "agent": preview["merged"]}
    state["undo"] = {"before": before, "after": after, "before_base": before_base,
                     "after_base": before_base, "before_heads": before_heads, "after_heads": before_heads,
                     "action": direction}
    # Refuse to execute if another previous operation was interrupted.
    state["review"] = None
    state["pending_operation"] = direction
    save(file, state)
    try:
        if direction == "sync":
            apply_tree(repo, source, before["desktop"], after["desktop"])
        apply_tree(repo, agent, before["agent"], after["agent"])
        # Verify worktrees match the intended tree after applying changes.
        _, st, _ = snapshot(repo, source, before_base, "Post apply desktop", state.get("exclude", []))
        _, at, _ = snapshot(repo, agent, before_base, "Post apply agent", state.get("exclude", []))
        if st != after["desktop"] or at != after["agent"]:
            raise SyncError("Filesystem changed during application, aborting")
        if direction == "sync":
            newbase = out(repo, "commit-tree", preview["merged"], "-p", preview["desktop_commit"], "-p", preview["agent_commit"], "-m", "Accepted synchronization")
            git(repo, "update-ref", "refs/hermes/base", newbase)
            state["base"] = newbase
            state["desktop_head"] = newbase
            state["agent_head"] = newbase
            state["undo"]["after_base"] = newbase
        else:
            # A PUSH is a real merge on the Hermes side. Record both parent
            # commits so subsequent PULLs have the correct merge-base.
            agent_head = out(repo, "commit-tree", preview["merged"],
                             "-p", preview["desktop_commit"], "-p", preview["agent_commit"],
                             "-m", "Accepted desktop changes into Hermes")
            state["desktop_head"] = preview["desktop_commit"]
            state["agent_head"] = agent_head
        state["undo"]["after_heads"] = {"desktop": state["desktop_head"], "agent": state["agent_head"]}
        for which, tree in before.items():
            git(repo, "update-ref", f"refs/hermes/undo_{which}", tree)  # refs may point to tree objects
        state.pop("pending_operation", None)
        save(file, state)
        if direction == "sync":
            clear_resolution(file, state)
            remove_gui_review(file, state)
            save(file, state)
    except Exception as exc:
        notice_error("Apply failed; attempting rollback.", "yellow")
        try:
            _, current_s, _ = snapshot(repo, source, before_base, "Failure snapshot desktop", state.get("exclude", []))
            _, current_a, _ = snapshot(repo, agent, before_base, "Failure snapshot agent", state.get("exclude", []))
            apply_tree(repo, source, current_s, before["desktop"])
            apply_tree(repo, agent, current_a, before["agent"])
            state.pop("pending_operation", None)
            state["base"] = before_base
            state["desktop_head"], state["agent_head"] = before_heads["desktop"], before_heads["agent"]
            git(repo, "update-ref", "refs/hermes/base", before_base)
            state["undo"] = None
            save(file, state)
        except Exception as rollback_error:
            notice_error(f"Rollback failed: {rollback_error}. Run hermes recover or repair manually.")
        raise SyncError(f"Apply failed: {exc}") from exc
    notice(("Both workspaces synchronized." if direction == "sync" else "Desktop changes sent to Hermes.") + "  Undo: hermes undo")


def do_drop(file: Path, state: dict, args):
    """Discard all *managed* agent changes, replacing them with desktop files.

    This is intentionally NOT a merge: it works even when the two sides
    conflict. Record a new joint ancestor to keep rejected agent edits from
    reappearing on the next push/pull. The operation has a one-step undo.
    """
    repo = Path(state["repo"])
    source, agent = get_worktrees(state)
    patterns = state.get("exclude", [])
    previous_base = state["base"]
    previous_heads = {"desktop": state.get("desktop_head", previous_base),
                      "agent": state.get("agent_head", previous_base)}
    src_commit, src_tree, _ = snapshot(repo, source, previous_heads["desktop"],
                                       "Desktop before DROP", patterns)
    agent_commit, agent_tree, _ = snapshot(repo, agent, previous_heads["agent"],
                                           "Hermes before DROP", patterns)
    # No Git merge here: the user is explicitly rejecting agent-side work.
    changes = git(repo, "diff", "--name-status", agent_tree, src_tree).stdout.decode(errors="replace")
    if agent_tree == src_tree and out(repo, "rev-parse", f"{previous_base}^{{tree}}") == src_tree:
        if state.get("gui_review"):
            remove_gui_review(file, state)
            save(file, state)
        notice("Hermes already matches desktop.")
        return
    heading(f"Drop Hermes changes · {state['name']}")
    notice("Hermes managed files will be replaced with desktop versions.", "yellow")
    show_changes(repo, agent_tree, src_tree, "Hermes files", 8)
    info("Desktop and original .git remain untouched. Excluded files remain.")
    if not args.yes:
        if not sys.stdin.isatty():
            raise SyncError("Confirmation required. Rerun interactively or use hermes drop --yes.")
        choice = _STDOUT.input(Text(f"Type DROP to discard Hermes-side changes to {state['name']}: ", style="bold yellow")).strip()
        if choice != "DROP":
            notice("Cancelled. Workspaces unchanged.", "cyan")
            return

    before = {"desktop": src_tree, "agent": agent_tree}
    after = {"desktop": src_tree, "agent": src_tree}
    state["undo"] = {"before": before, "after": after,
                     "before_base": previous_base, "after_base": previous_base,
                     "before_heads": previous_heads, "after_heads": previous_heads,
                     "action": "drop"}
    state["review"] = None
    state["pending_operation"] = "drop"
    save(file, state)
    try:
        apply_tree(repo, agent, agent_tree, src_tree)
        # Guard against external edits during the operation.
        _, src_after, _ = snapshot(repo, source, previous_base, "Post drop desktop", patterns)
        _, agent_after, _ = snapshot(repo, agent, previous_base, "Post drop agent", patterns)
        if src_after != src_tree or agent_after != src_tree:
            raise SyncError("Files changed during DROP; refusing to finalize")
        newbase = out(repo, "commit-tree", src_tree, "-p", src_commit, "-p", agent_commit,
                      "-m", "Drop Hermes edits and adopt desktop snapshot")
        git(repo, "update-ref", "refs/hermes/base", newbase)
        state["base"] = newbase
        state["desktop_head"] = newbase
        state["agent_head"] = newbase
        state["undo"]["after_base"] = newbase
        state["undo"]["after_heads"] = {"desktop": newbase, "agent": newbase}
        # A prepared resolution is now obsolete.
        clear_resolution(file, state)
        remove_gui_review(file, state)
        state.pop("pending_operation", None)
        save(file, state)
    except Exception as exc:
        notice_error("Drop failed; attempting rollback.", "yellow")
        try:
            _, current, _ = snapshot(repo, agent, previous_base, "Failed DROP", patterns)
            apply_tree(repo, agent, current, agent_tree)
            state["base"] = previous_base
            state["desktop_head"] = previous_heads["desktop"]
            state["agent_head"] = previous_heads["agent"]
            git(repo, "update-ref", "refs/hermes/base", previous_base)
            state.pop("pending_operation", None)
            state["undo"] = None
            save(file, state)
        except Exception as rollback_error:
            notice_error(f"Rollback failed: {rollback_error}. Run hermes recover.")
        raise SyncError(f"Drop failed: {exc}") from exc
    notice("Hermes now matches desktop (managed files). Undo: hermes undo")


def do_undo(file: Path, state: dict):
    undo = state.get("undo")
    if not undo:
        raise SyncError("No synchronization to undo.")
    if state.get("pending_operation"):
        raise SyncError("An interrupted operation is pending. Resolve it before another sync.")
    repo = Path(state["repo"])
    source, agent = get_worktrees(state)
    _, st, _ = snapshot(repo, source, state["base"], "Undo check desktop", state.get("exclude", []))
    _, at, _ = snapshot(repo, agent, state["base"], "Undo check agent", state.get("exclude", []))
    if st != undo["after"]["desktop"] or at != undo["after"]["agent"]:
        raise SyncError("Files changed after synchronization. Undo refused to protect newer edits.")
    apply_tree(repo, source, st, undo["before"]["desktop"])
    apply_tree(repo, agent, at, undo["before"]["agent"])
    git(repo, "update-ref", "refs/hermes/base", undo["before_base"])
    state["base"] = undo["before_base"]
    heads = undo.get("before_heads", {"desktop": undo["before_base"], "agent": undo["before_base"]})
    state["desktop_head"] = heads["desktop"]
    state["agent_head"] = heads["agent"]
    state["undo"] = None
    state["review"] = None
    remove_gui_review(file, state)
    save(file, state)
    notice(f"Undid {undo['action']}. Restored previous working copies.")


def do_recover(file: Path, state: dict):
    """Fail-closed recovery of interrupted apply when all entries match either
    known pre- or post-apply versions. Unknown changes are never overwritten.
    """
    action = state.get("pending_operation")
    if not action:
        notice("Nothing to recover.", "cyan")
        return
    record = state.get("undo")
    if not record:
        raise SyncError("Pending operation has no recovery record. Inspect state.json manually.")
    repo = Path(state["repo"])
    source, agent = get_worktrees(state)
    for kind, root in (("desktop", source), ("agent", agent)):
        _, cur_tree, _ = snapshot(repo, root, state["base"], "Recovery scan", state.get("exclude", []))
        cur = tree_files(repo, cur_tree)
        before = tree_files(repo, record["before"][kind])
        after = tree_files(repo, record["after"][kind])
        for rel in sorted(set(cur) | set(before) | set(after)):
            if cur.get(rel) not in (before.get(rel), after.get(rel)):
                raise SyncError(f"Recovery refused: unexpected file contents in {kind}/{rel}.")
    for kind, root in (("desktop", source), ("agent", agent)):
        _, cur, _ = snapshot(repo, root, state["base"], "Recovery rollback scan", state.get("exclude", []))
        apply_tree(repo, root, cur, record["before"][kind])
    git(repo, "update-ref", "refs/hermes/base", record["before_base"])
    state["base"] = record["before_base"]
    heads = record.get("before_heads", {"desktop": state["base"], "agent": state["base"]})
    state["desktop_head"], state["agent_head"] = heads["desktop"], heads["agent"]
    state["undo"] = None
    state["review"] = None
    state.pop("pending_operation", None)
    save(file, state)
    notice("Recovered pre-operation working copies.")


# Docker project isolation. No Git operation is involved in switching mounts.
_IDLE_NAME = "_inactive"
_PROJECT_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,80}\Z")


def _runtime_env() -> Path:
    dotenv = BASE / ".env"
    if not dotenv.is_file() or dotenv.is_symlink():
        raise SyncError(f"Missing or unsafe Docker environment: {dotenv}. Run the portable installer.")
    return dotenv


def _read_runtime_values() -> dict[str, str]:
    # Only handles the installer's well-defined, single-line path fields. Other
    # Compose .env variables are left entirely unchanged.
    values = {}
    for line in _runtime_env().read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"(HERMES_[A-Z_]+)=(.*)", line.strip())
        if match:
            values[match.group(1)] = match.group(2).strip().strip("\"'")
    return values


def _write_active_environment(name: str, folder: Path) -> None:
    dotenv = _runtime_env()
    text = dotenv.read_text(encoding="utf-8")
    updates = {"HERMES_ACTIVE_PROJECT": name, "HERMES_ACTIVE_WORKSPACE": str(folder)}
    for key, value in updates.items():
        if "\n" in value or "\r" in value or "'" in value or "\\" in value:
            raise SyncError("Unsupported project workspace path characters.")
        if re.search(rf"(?m)^{key}=", text):
            text = re.sub(rf"(?m)^{key}=.*$", lambda _: f"{key}='{value}'", text)
        else:
            text += ("" if text.endswith("\n") else "\n") + f"{key}='{value}'\n"
    # Atomic file replacement; do not overwrite credentials or other variables.
    fd, tmp = tempfile.mkstemp(prefix=".env.", dir=BASE)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, dotenv)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _current_active() -> str:
    return _read_runtime_values().get("HERMES_ACTIVE_PROJECT", _IDLE_NAME)


def _validated_active(name: str) -> Path:
    if name == _IDLE_NAME:
        idle = BASE / "idle-workspace"
        if not idle.is_dir() or idle.is_symlink():
            raise SyncError("Inactive placeholder is missing or unsafe. Rerun setup.sh.")
        return idle
    if not _PROJECT_NAME_RE.fullmatch(name):
        raise SyncError("Invalid project name.")
    state_file = config_path(name)
    if not state_file.is_file() or state_file.is_symlink():
        raise SyncError(f"Unknown project: {name}. Use hermes list.")
    state = json.loads(state_file.read_text(encoding="utf-8"))
    folder = WORKSPACES / name
    if (folder.is_symlink() or not folder.is_dir() or WORKSPACES.is_symlink()
            or Path(state.get("agent", "")) != folder
            or state.get("name") != name):
        raise SyncError(f"Unsafe or missing agent workspace for {name}.")
    return folder


def _checked_compose() -> Path:
    compose = BASE / "compose.yaml"
    if not compose.is_file() or compose.is_symlink():
        raise SyncError(f"Docker setup not found at {compose}. Run the portable installer first.")
    if not shutil.which("docker"):
        raise SyncError("Docker is not installed or is missing from PATH.")
    existing = subprocess.run(
        ["docker", "inspect", "-f", '{{index .Config.Labels "com.docker.compose.project.working_dir"}}', "hermes-webui"],
        capture_output=True, text=True,
    )
    if existing.returncode == 0:
        origin = existing.stdout.strip()
        if origin and origin != "<no value>" and Path(origin).resolve() != BASE:
            raise SyncError(f"hermes-webui belongs to a different Compose project: {origin}")
    return compose


def _require_isolated_compose() -> None:
    # Refuse using the new selector with an old multi-project Compose mount.
    compose = (BASE / "compose.yaml").read_text(encoding="utf-8")
    required = ("source: ${HERMES_ACTIVE_WORKSPACE}",
                "target: /workspace/${HERMES_ACTIVE_PROJECT}",
                "source: ${HERMES_TOOLS_HOME}")
    if any(item not in compose for item in required) or "source: ${HERMES_WORKSPACES}" in compose:
        raise SyncError("Docker Compose still uses the old workspace layout. Update via setup.sh --replace-compose first.")


def _compose_run(*args: str):
    compose = _checked_compose()
    command = ["docker", "compose", "--project-directory", str(BASE), "-f", str(compose), *args]
    try:
        result = subprocess.run(command, cwd=BASE, capture_output=True, text=True)
    except OSError as exc:
        raise SyncError(f"Docker command failed: {exc}") from exc
    if result.returncode:
        details = (result.stderr.strip() or result.stdout.strip()).splitlines()
        reason = details[-1] if details else "Unknown Docker Compose error"
        raise SyncError(f"Docker {' '.join(args)} failed: {reason}")
    return result


def _container_running() -> bool:
    p = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", "hermes-webui"],
                       capture_output=True, text=True)
    return p.returncode == 0 and p.stdout.strip() == "true"


def do_use(args):
    """Switch the only project mount. Preserve config, history, and tools."""
    target = _IDLE_NAME if args.none else args.project_name
    if not args.none and target == _IDLE_NAME:
        raise SyncError("Reserved workspace name. Use hermes use --none instead.")
    folder = _validated_active(target)
    _checked_compose()
    _require_isolated_compose()
    old = _current_active()
    if old == target:
        notice(f"Active workspace: {target if not args.none else '(none)'}.")
        return
    was_running = _container_running()
    # Explicit checkpoint rule. Do not silently cancel an active task.
    heading("Switch workspace")
    label("From", old if old != _IDLE_NAME else "(none)")
    label("To", target if target != _IDLE_NAME else "(none)")
    if was_running and not args.yes:
        if not sys.stdin.isatty():
            raise SyncError("Switch requires confirmation while WebUI is running. Use --yes at a checkpoint.")
        choice = _STDOUT.input(Text("Recreate Hermes Docker container now? [y/N] ", style="bold yellow")).strip().lower()
        if choice not in ("y", "yes"):
            notice("Cancelled.", "cyan")
            return
    old_folder = _validated_active(old) if old != target else folder
    _write_active_environment(target, folder)
    try:
        _compose_run("config", "--quiet")
        if was_running:
            _compose_run("up", "-d", "--force-recreate", "--no-deps", "webui")
    except (SyncError, OSError):
        # Restore selection and best-effort container state on failed recreation.
        _write_active_environment(old, old_folder)
        if was_running:
            try:
                _compose_run("up", "-d", "--force-recreate", "--no-deps", "webui")
            except (SyncError, OSError):
                notice_error("Failed to restore previous container; inspect Docker manually.", "yellow")
        raise
    notice(f"Active workspace: {target if target != _IDLE_NAME else '(none)'}.")
    info(f"WebUI workspace: /workspace/{target}")
    if not was_running:
        info("Docker is stopped. Run hermes boot when ready.")


def do_boot():
    """Start the installed Docker Compose stack; independent of any sync project."""
    compose = _checked_compose()
    _require_isolated_compose()
    active = _current_active()
    selected = _validated_active(active)
    settings = _read_runtime_values()
    if (settings.get("HERMES_ACTIVE_WORKSPACE") != str(selected)
            or settings.get("HERMES_TOOLS_HOME") != str(BASE / "tools")):
        raise SyncError("Unsafe Docker mount selection. Rerun setup.sh and use hermes use NAME.")
    # --project-directory makes this independent of the caller's CWD.
    command = ["docker", "compose", "--project-directory", str(BASE),
               "-f", str(compose), "up", "-d"]
    try:
        result = subprocess.run(command, cwd=BASE, capture_output=True, text=True)
    except OSError as exc:
        raise SyncError(f"Unable to start Docker: {exc}") from exc
    if result.returncode:
        details = (result.stderr.strip() or result.stdout.strip()).splitlines()
        reason = details[-1] if details else "Unknown Docker Compose error"
        raise SyncError(f"Docker startup failed: {reason}")

    notice("Hermes Docker started.")
    # Read the host-published port from the installer's non-secret dotenv metadata.
    port = "8787"
    dotenv = BASE / ".env"
    if dotenv.is_file():
        for line in dotenv.read_text(encoding="utf-8").splitlines():
            match = re.fullmatch(r"\s*WEBUI_PORT\s*=\s*['\"]?(\d+)['\"]?\s*", line)
            if match:
                port = match.group(1)
                break
    info(f"WebUI: http://localhost:{port}")


def do_stop():
    """Stop only the installed Compose services; preserve containers and files."""
    compose = BASE / "compose.yaml"
    if not compose.is_file() or compose.is_symlink():
        raise SyncError(f"Docker setup not found at {compose}. Run the portable installer first.")
    if not shutil.which("docker"):
        raise SyncError("Docker is not installed or is missing from PATH.")

    # Mirror boot's ownership check: never operate on another installation.
    existing = subprocess.run(
        ["docker", "inspect", "-f", '{{index .Config.Labels "com.docker.compose.project.working_dir"}}', "hermes-webui"],
        capture_output=True, text=True,
    )
    if existing.returncode == 0:
        origin = existing.stdout.strip()
        if origin and origin != "<no value>" and Path(origin).resolve() != BASE:
            raise SyncError(f"hermes-webui belongs to a different Compose project: {origin}")

    command = ["docker", "compose", "--project-directory", str(BASE),
               "-f", str(compose), "stop"]
    try:
        result = subprocess.run(command, cwd=BASE, capture_output=True, text=True)
    except OSError as exc:
        raise SyncError(f"Unable to stop Docker: {exc}") from exc
    if result.returncode:
        details = (result.stderr.strip() or result.stdout.strip()).splitlines()
        reason = details[-1] if details else "Unknown Docker Compose error"
        raise SyncError(f"Docker shutdown failed: {reason}")

    notice("Hermes Docker stopped.")
    info("Workspaces, credentials and sync history preserved.")


def main():
    parser = RichArgumentParser(prog="hermes", description="Private Git-based Hermes workspace synchronization")
    parser.add_argument("--version", nargs=0, action=RichVersionAction, help="Show version and exit")
    sub = parser.add_subparsers(dest="command", required=True, parser_class=RichArgumentParser)
    sub.add_parser("boot", help="Start Hermes Agent and WebUI in Docker")
    sub.add_parser("stop", help="Stop Hermes Docker services; preserve all data")
    use = sub.add_parser("use", help="Mount only one project in WebUI (or select none)")
    use.add_argument("project_name", nargs="?", help="Registered project to expose inside Docker")
    use.add_argument("--none", action="store_true", help="Unmount the active project (show empty workspace)")
    use.add_argument("--yes", action="store_true", help="Recreate the running Docker container without prompting")
    share = sub.add_parser("share", help="Preview or initialize an isolated project copy")
    share.add_argument("path", nargs="?", default=".")
    share.add_argument("--name")
    share.add_argument("--yes", action="store_true", help="Create the copy after reviewing share plan")
    share.add_argument("--verbose", action="store_true", help="Show the complete file inventory")
    share.add_argument("--exclude", action="append", default=[], help="Additional project-relative glob exclusion; repeatable, set at share time")
    review = sub.add_parser("review", help="Preview and record a merge; does not modify files")
    review.add_argument("--patch", action="store_true", help="Show the proposed desktop diff")
    review.add_argument("--gui", action="store_true", help="Review/edit the proposed merge in VS Code")
    review.add_argument("--reset-gui", action="store_true", help="Discard GUI edits and regenerate the review; requires --gui")
    review.add_argument("--abort-gui", action="store_true", help="Discard the GUI review without modifying either workspace")
    for cmd in ("sync", "pull", "push", "undo", "status", "recover"):
        sub.add_parser(cmd, help=({"sync": "Merge desktop and Hermes into a shared checkpoint",
                                   "pull": "Deprecated alias of sync", "push": "Merge desktop changes into Hermes only",
                                   "undo": "Undo the last sync/push/drop", "status": "Summarize pending changes",
                                   "recover": "Recover a stopped operation"}[cmd]))
    sub.choices["status"].add_argument("--verbose", action="store_true", help="Show all changed files")
    sub.add_parser("list", help="Show registered projects, including missing desktop sources")
    purge = sub.add_parser("purge", help="Permanently remove one agent workspace and its private sync history")
    purge.add_argument("--yes", action="store_true", help="Delete without interactive confirmation")
    drop = sub.add_parser("drop", help="Discard agent changes and replace managed agent files with desktop files")
    drop.add_argument("--yes", action="store_true", help="Skip the destructive-operation confirmation")
    resolve = sub.add_parser("resolve", help="Prepare, finish or abort isolated manual conflict resolution")
    exclusive = resolve.add_mutually_exclusive_group()
    exclusive.add_argument("--continue", dest="continue_resolution", action="store_true")
    exclusive.add_argument("--abort", action="store_true")
    for subcommand in (review, resolve, *(sub.choices[k] for k in ("sync", "pull", "push", "undo", "status", "recover", "drop", "purge"))):
        subcommand.add_argument("--project", help="Select a registered project by name (otherwise current directory)")
    argv = sys.argv[1:]
    called_as = Path(sys.argv[0]).name
    # `hermes-sync` itself is the two-way sync command for backwards compatibility.
    # subcommands for test runners and scripts; `pull` remains a legacy alias.
    aliases = {"hermes-share", "hermes-review", "hermes-push", "hermes-pull",
               "hermes-undo", "hermes-resolve", "hermes-status", "hermes-recover", "hermes-drop"}
    if called_as in aliases:
        argv.insert(0, called_as.removeprefix("hermes-"))
    elif called_as == "hermes-sync" and (not argv or argv[0].startswith("-")):
        argv.insert(0, "sync")
    if called_as == "hermes" and not argv:
        parser.print_help()
        return
    args = parser.parse_args(argv)
    if args.command == "review" and args.reset_gui and not args.gui:
        parser.error("--reset-gui requires --gui")
    if args.command == "use":
        if args.none == bool(args.project_name):
            parser.error("Specify a project name OR --none")
        do_use(args)
        return
    if args.command == "boot":
        do_boot()
        return
    if args.command == "stop":
        do_stop()
        return
    if args.command == "share":
        do_share(args)
        return
    if args.command == "list":
        do_list()
        return
    if args.command == "pull":
        args.command = "sync"  # Legacy alias, same state and semantics.
    file, state = load_project(Path.cwd(), args.project)
    # Purge only unregisters/removes the agent project; ignore rules and Git
    # snapshots are irrelevant. In particular, allow safe removal of damaged
    # registrations without requiring a valid common-base commit.
    ignore_context = (nullcontext() if args.command == "purge"
                      else ignore_scope(Path(state["source"]), Path(state["agent"]), state))
    with locked(file), ignore_context:
        if state.get("pending_operation") and args.command != "recover":
            raise SyncError("Previous synchronization was interrupted. Run hermes recover (at a checkpoint).")
        if args.command == "recover":
            do_recover(file, state)
        elif args.command == "status":
            do_status(state, verbose=args.verbose)
        elif args.command == "resolve":
            do_resolve(file, state, args)
        elif args.command == "review":
            do_review(file, state, args)
        elif args.command == "undo":
            do_undo(file, state)
        elif args.command == "drop":
            do_drop(file, state, args)
        elif args.command == "purge":
            do_purge(file, state, args)
        else:
            do_apply(file, state, args.command)


if __name__ == "__main__":
    try:
        main()
    except (SyncError, OSError) as e:
        notice_error(str(e))
        sys.exit(1)
