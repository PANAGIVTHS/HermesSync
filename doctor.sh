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
for dir in "$ROOT/agent-src" "$ROOT/workspaces" "$ROOT/sync/projects" "$ROOT/data/.hermes"; do
  if [[ -d "$dir" ]]; then ok "Directory: $dir"; else no "Missing: $dir"; fi
done
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
