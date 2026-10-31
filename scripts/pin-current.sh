#!/usr/bin/env bash
# Capture the exact running WebUI image digest and the local Agent commit.
# This does not restart containers, edit sources or touch any workspace.
set -Eeuo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
ROOT="$HOME/Hermes"
[[ -d "$ROOT/agent-src/.git" && -f "$ROOT/.env" ]] || { echo 'Missing Hermes runtime checkout or .env' >&2; exit 1; }
[[ -z "$(git -C "$ROOT/agent-src" status --porcelain)" ]] || { echo 'Agent checkout has local modifications; inspect first.' >&2; exit 1; }
agent_sha="$(git -C "$ROOT/agent-src" rev-parse HEAD)"
image_id="$(docker inspect --format '{{.Image}}' hermes-webui)"
repo_digests="$(docker image inspect --format '{{json .RepoDigests}}' "$image_id")"
webui_image="$(python3 -c '
import json,sys
refs = json.load(sys.stdin) or []
matched = [x for x in refs if x.startswith("ghcr.io/nesquena/hermes-webui@sha256:")]
if len(matched) != 1:
    raise SystemExit("No single registry digest available for this RUNNING WebUI image: " + repr(refs))
print(matched[0])
' <<< "$repo_digests")"
[[ "$agent_sha" =~ ^[0-9a-f]{40}$ ]]
[[ "$webui_image" =~ ^ghcr\.io/nesquena/hermes-webui@sha256:[0-9a-f]{64}$ ]]
python3 - "$HERE/versions.lock" "$ROOT/.env" "$agent_sha" "$webui_image" <<'PY_WRITE_LOCK'
from pathlib import Path
from datetime import datetime
import os, sys
lock, env = Path(sys.argv[1]), Path(sys.argv[2])
sha, image = sys.argv[3:5]
if lock.is_symlink() or env.is_symlink():
    raise SystemExit('Refusing symlinked configuration files')
original = env.read_text()
lines = original.splitlines(keepends=True)
found = False
for i, line in enumerate(lines):
    if line.startswith('WEBUI_IMAGE='):
        if found:
            raise SystemExit('Duplicate WEBUI_IMAGE in .env')
        found = True
        lines[i] = f'WEBUI_IMAGE={image}\n'
if not found:
    lines.append(('' if not original or original.endswith('\n') else '\n') + f'WEBUI_IMAGE={image}\n')
updated = ''.join(lines)
if updated != original:
    backup = env.with_name('.env.before-pin.' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    backup.write_text(original)
    backup.chmod(0o600)
    tmp = env.with_name('.env.pin-tmp')
    fd = os.open(tmp, os.O_CREAT|os.O_EXCL|os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(updated)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, env)
    finally:
        tmp.unlink(missing_ok=True)
lock.write_text('# Generated on working desktop; no credentials.\n' +
                f'HERMES_AGENT_COMMIT={sha}\nWEBUI_IMAGE={image}\n')
lock.chmod(0o644)
PY_WRITE_LOCK
printf 'Pinned Agent commit: %s\n' "$agent_sha"
printf 'Pinned running WebUI: %s\n' "$webui_image"
printf 'Wrote versions.lock and updated runtime .env; no container restart.\n'
printf 'Commit versions.lock so laptops install these exact versions.\n'
