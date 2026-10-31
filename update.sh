#!/usr/bin/env bash
# Reapply pinned host CLI and Dockerized Hermes Agent/WebUI. Verify Skills API afterwards.
# Never removes persisted skills, workspaces, credentials, or synchronization state.
set -Eeuo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT="$HOME/Hermes"

no_start=0
for arg in "$@"; do
  [[ "$arg" == --no-start ]] && no_start=1
done

# setup.sh verifies pinned Agent source, pulls only the locked WebUI digest,
# and recreates the WebUI container only if its configuration/image changed.
# Use --replace-workflow explicitly when you also want the repo's updated guidance.
"$HERE/setup.sh" --replace-compose "$@"

# --no-start is used for offline/controlled maintenance. No running WebUI to check.
if (( no_start )); then
  printf 'Skills API check skipped (--no-start).\n'
  exit 0
fi

if ! command -v curl >/dev/null 2>&1; then
  printf 'Skills API check skipped (curl unavailable). Verify /api/skills manually.\n' >&2
  exit 0
fi

# Compose knows the published host port even when WEBUI_PORT was customized.
binding="$(cd "$ROOT" && docker compose port webui 8787 2>/dev/null || true)"
if [[ ! "$binding" =~ ^127\.0\.0\.1:[0-9]+$ ]]; then
  printf 'Could not determine local WebUI port; inspect: docker compose -f "%s/compose.yaml" ps\n' "$ROOT" >&2
  exit 1
fi

url="http://${binding}/api/skills"
response_code=''
for ((attempt=0; attempt<30; attempt++)); do
  response_code="$(curl --silent --max-time 4 --output /dev/null --write-out '%{http_code}' "$url" 2>/dev/null || true)"
  [[ "$response_code" == 200 ]] && break
  sleep 2
done
if [[ "$response_code" == 200 ]]; then
  printf '✓ WebUI Skills API is healthy (HTTP 200).\n'
else
  printf '✗ WebUI Skills API returned HTTP %s after update.\n' "${response_code:-unavailable}" >&2
  printf '  Your installed skills are not deleted by this check. Inspect:\n' >&2
  printf '  docker compose -f "%s/compose.yaml" logs --tail=100 webui\n' "$ROOT" >&2
  exit 1
fi
