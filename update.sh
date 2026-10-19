#!/usr/bin/env bash
# Update CLI/Compose from current repo and optionally upstream agent source.
set -Eeuo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# Existing Compose is backed up before applying a changed repo template.
"$HERE/setup.sh" --replace-compose --update-agent "$@"
