#!/usr/bin/env bash
# Non-mutating verification (except an optional temporary file for bind-mount check).
set -Eeuo pipefail
ROOT="$HOME/Hermes"
BIN="$HOME/.local/bin/hermes"
fail=0
ok() { printf '  \033[32m✓\033[0m %s\n' "$*"; }
no() { printf '  \033[31m✗\033[0m %s\n' "$*"; fail=1; }
info() { printf '  \033[36m→\033[0m %s\n' "$*"; }
check() { if "$@" >/dev/null 2>&1; then ok "$*"; else no "$*"; fi; }
printf 'Hermes Portable · Diagnostics\n\n'
check git --version
check python3 --version
if [[ -x "$BIN" ]]; then
  check "$BIN" --version
  check "$BIN" list
else
  no "Host sync CLI missing: $BIN"
fi
if [[ -e "$HOME/.hermes" ]]; then
  info 'A native ~/.hermes directory exists. This setup never uses or deletes it.'
else
  ok 'No native ~/.hermes directory'
fi
for dir in "$ROOT/agent-src" "$ROOT/workspaces" "$ROOT/sync/projects" "$ROOT/data/.hermes" "$ROOT/tools" "$ROOT/idle-workspace"; do
  if [[ -d "$dir" ]]; then ok "Directory: $dir"; else no "Missing: $dir"; fi
done
# Inspect managed workflow without printing private provider settings.
if [[ -f "$ROOT/data/.hermes/SOUL.md" && -f "$ROOT/data/.hermes/WORKFLOW.md" ]]; then
  ok 'Instance SOUL and workflow files present'
else
  no 'Missing instance SOUL or WORKFLOW.md'
fi
if [[ -x "$HOME/.local/share/hermes-sync/venv/bin/python" ]] && \
   "$HOME/.local/share/hermes-sync/venv/bin/python" - "$ROOT/data/.hermes/config.yaml" <<'PYGUIDANCE' >/dev/null 2>&1
import sys
from pathlib import Path
from ruamel.yaml import YAML
config = YAML(typ='safe').load(Path(sys.argv[1]).read_text())
prompt = config.get('agent', {}).get('system_prompt', '')
assert prompt.count('<!-- BEGIN HERMES PORTABLE WORKFLOW -->') == 1
assert prompt.count('<!-- END HERMES PORTABLE WORKFLOW -->') == 1
PYGUIDANCE
then ok 'Instance workflow injected into agent.system_prompt'
else no 'Instance workflow injection missing or invalid'
fi
if [[ -f "$ROOT/tools/env.sh" ]]; then
  if HERMES_TOOLS_HOME="$ROOT/tools" BASH_ENV="$ROOT/tools/env.sh" bash -c        '[[ ":$PATH:" == *":$HERMES_TOOLS_HOME/bin:"* && "$GRADLE_USER_HOME" == "$HERMES_TOOLS_HOME/caches/gradle" ]]' >/dev/null 2>&1; then
    ok 'Persistent tools environment loads in Bash'
  else
    no 'Persistent tools environment failed Bash startup check'
  fi
else
  no 'Missing persistent tools env.sh'
fi
if [[ -f "$ROOT/.env" && -f "$ROOT/compose.yaml" ]]; then
  (cd "$ROOT" && docker compose config --quiet) >/dev/null 2>&1 && ok 'Compose configuration valid' || no 'Compose configuration invalid'
else
  no 'Docker Compose runtime files missing'
fi
if ! docker info >/dev/null 2>&1; then
  no 'Docker daemon inaccessible'
else
  check docker compose -f "$ROOT/compose.yaml" ps
  if [[ -f "$ROOT/compose.yaml" ]] && (cd "$ROOT" && docker compose ps --status running --services 2>/dev/null | grep -qx webui); then
    ok 'WebUI container running'
    # Check actual mounts, not just WebUI's selected working directory.
    mount_report="$(docker inspect hermes-webui --format '{{json .Mounts}}' 2>/dev/null || true)"
    if [[ -n "$mount_report" ]] && MOUNT_REPORT="$mount_report" ROOT="$ROOT" python3 - <<'PYMOUNT'
import json, os
from pathlib import Path
mounts = json.loads(os.environ["MOUNT_REPORT"])
root = Path(os.environ["ROOT"])
project_mounts = [m for m in mounts if m.get("Destination", "").startswith("/workspace/")]
assert len(project_mounts) == 1, f"expected one project mount, got {len(project_mounts)}"
assert not any(m.get("Source") == str(root / "workspaces") for m in mounts)
assert any(m.get("Source") == str(root / "tools") and m.get("Destination") == "/opt/hermes-tools" for m in mounts)
PYMOUNT
    then ok 'Only one workspace mounted; shared tools available'
    else no 'Unexpected Docker mount layout (old multi-project container?)'
    fi
  else
    no 'WebUI container not running'
  fi
  if command -v curl >/dev/null 2>&1; then
    if curl --max-time 5 -fsS http://127.0.0.1:8787/health >/dev/null 2>&1; then
      ok 'WebUI HTTP health endpoint responds'
      # Never display token or config contents.
      curl --max-time 8 -fsS http://127.0.0.1:8787/api/onboarding/status 2>/dev/null | python3 -c '
import json,sys
try:
 data=json.load(sys.stdin).get("system",{})
 for name in ("hermes_found", "imports_ok", "provider_configured", "provider_ready", "chat_ready"):
  print("  {} {}".format("✓" if data.get(name) else "-",name))
except Exception: print("  - Provider status unavailable (check WebUI manually)")
' || true
    else
      no 'WebUI HTTP health endpoint not yet ready (first boot may take minutes)'
    fi
  fi
fi
printf '\n'
if ((fail)); then
  printf 'Some checks need attention. Inspect: cd ~/Hermes && docker compose logs --tail=80\n'
  exit 1
fi
printf 'Basic checks passed. OAuth / model readiness shown above if available.\n'
