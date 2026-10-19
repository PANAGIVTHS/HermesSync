#!/usr/bin/env bash
# One entry point for a new Linux machine. OS provisioning requires explicit consent.
set -Eeuo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
if ! command -v git >/dev/null 2>&1 || ! command -v python3 >/dev/null 2>&1 || \
   ! command -v docker >/dev/null 2>&1 || \
   ! docker compose version >/dev/null 2>&1; then
  printf 'Some prerequisites are missing. The optional OS installer will request permission before using sudo.\n'
  "$HERE/scripts/install-docker.sh"
fi
if ! docker info >/dev/null 2>&1; then
  printf '\nDocker is installed but is not accessible from your user session.\n' >&2
  printf 'Start Docker, then log out/in if you were added to the docker group. Re-run ./bootstrap.sh.\n' >&2
  exit 1
fi
exec "$HERE/setup.sh" "$@"
