#!/usr/bin/env bash
# Install local Hermes Sync CLI + Dockerized Hermes Agent/WebUI.
# Never installs Hermes Agent natively; never deletes project data.
set -Eeuo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT="$HOME/Hermes"
CLI_HOME="$HOME/.local/share/hermes-sync"
BIN_DIR="$HOME/.local/bin"
VENV="$CLI_HOME/venv"
CLI_TARGET="$CLI_HOME/bin/hermes"
YES=0
REPLACE_COMPOSE=0
NO_START=0
UPDATE_AGENT=0
REPLACE_LAUNCHER=0

say()  { printf '\033[36m→\033[0m %s\n' "$*"; }
pass() { printf '\033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '\033[33m!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }
usage() {
  cat <<'USAGE'
Usage: ./setup.sh [options]

Install the portable Dockerized Hermes Agent + WebUI and host-side sync CLI.
No native Hermes Agent is installed.

Options:
  --yes                Confirm replacement of an existing host 'hermes' launcher
  --replace-launcher   Back up and replace an existing host 'hermes' launcher
  --replace-compose    Back up and replace an existing ~/Hermes/compose.yaml
  --update-agent       Fast-forward an existing agent-src checkout to upstream
  --no-start           Install files without running Docker Compose
  -h, --help           Show this help

Existing workspaces, sync history, credentials, and source repositories are preserved.
USAGE
}
for arg in "$@"; do
  case "$arg" in
    --yes) YES=1;;
    --replace-compose) REPLACE_COMPOSE=1;;
    --replace-launcher) REPLACE_LAUNCHER=1;;
    --no-start) NO_START=1;;
    --update-agent) UPDATE_AGENT=1;;
    -h|--help) usage; exit 0;;
    *) die "Unknown option: $arg";;
  esac
done

[[ "$(uname -s)" == Linux ]] || die 'This installer currently supports Linux only (Ubuntu/Arch).'
[[ "$(id -u)" -ne 0 ]] || die 'Run as your normal user, not root or sudo.'
[[ "$HOME" == /* ]] || die 'HOME must be an absolute path.'
[[ "$HOME" != *$'\n'* && "$HOME" != *$'\r'* ]] || die 'Unsupported newline in HOME path.'
if [[ "$HERE" == "$ROOT" || "$HERE" == "$ROOT/"* ]]; then
  die "This setup repository must live OUTSIDE $ROOT. Clone it elsewhere (e.g. ~/hermes-portable-setup)."
fi
for protected in "$ROOT" "$ROOT/data" "$ROOT/data/.hermes" "$ROOT/workspaces" "$ROOT/sync" "$ROOT/agent-src"; do
  [[ ! -L "$protected" ]] || die "Refusing symlinked runtime path: $protected (inspect manually)."
done
for program in git python3; do command -v "$program" >/dev/null 2>&1 || die "Missing $program. Run ./scripts/install-docker.sh for optional OS prerequisites."; done
if (( ! NO_START )); then
  command -v docker >/dev/null 2>&1 || die 'Docker missing. See ./scripts/install-docker.sh or docs/README.md.'
  docker compose version >/dev/null 2>&1 || die 'Docker Compose plugin missing.'
  docker info >/dev/null 2>&1 || die 'Cannot access Docker daemon. Start Docker / fix user group permissions (may require new login).'
fi
python3 - <<'PY' || die 'Python >=3.10 required'
import sys
assert sys.version_info >= (3, 10), sys.version
PY
IFS=. read -r git_major git_minor _ < <(git version | awk '{print $3}')
if ((git_major < 2 || (git_major == 2 && git_minor < 38))); then
  die 'Git >= 2.38 required by the private sync engine.'
fi

# Fail early if a different Compose file exists, instead of silently replacing it.
if [[ -e "$ROOT/compose.yaml" ]] && ! cmp -s "$HERE/docker/compose.yaml" "$ROOT/compose.yaml" && (( ! REPLACE_COMPOSE )); then
  die "Existing $ROOT/compose.yaml differs from the repo template. Re-run with --replace-compose to back it up and replace it, or review it manually."
fi

# An existing unrelated launcher must never be destroyed without permission.
if [[ -e "$BIN_DIR/hermes" || -L "$BIN_DIR/hermes" ]]; then
  linked="$(readlink -f -- "$BIN_DIR/hermes" 2>/dev/null || true)"
  if [[ "$linked" != "$CLI_TARGET" ]] && (( ! REPLACE_LAUNCHER && ! YES )); then
    die "Existing $BIN_DIR/hermes is not managed by this setup. Use --replace-launcher after reviewing its contents; its launcher will be backed up, not deleted."
  fi
fi
if [[ -e "$BIN_DIR/hermes-sync" || -L "$BIN_DIR/hermes-sync" ]]; then
  linked="$(readlink -f -- "$BIN_DIR/hermes-sync" 2>/dev/null || true)"
  if [[ "$linked" != "$CLI_TARGET" ]] && (( ! REPLACE_LAUNCHER && ! YES )); then
    die "Existing $BIN_DIR/hermes-sync is not managed by this setup. Use --replace-launcher to back it up and replace it."
  fi
fi

say 'Preparing isolated runtime directories'
mkdir -p "$ROOT/data/.hermes" "$ROOT/workspaces" "$ROOT/sync/projects" "$CLI_HOME/bin" "$BIN_DIR"
chmod 700 "$ROOT/data/.hermes"
if [[ ! -e "$ROOT/.env" ]]; then
  # Container variables only; secrets and OAuth credentials are written by the WebUI.
  # Simple absolute paths are encoded with single quotes for Compose dotenv format.
  [[ "$HOME" != *"'"* ]] || die "Unsupported single quote in HOME for Compose .env; configure paths manually."
  (umask 077
  cat > "$ROOT/.env" <<ENV
HERMES_UID=$(id -u)
HERMES_GID=$(id -g)
HERMES_DATA_DIR='$ROOT/data/.hermes'
HERMES_AGENT_SRC='$ROOT/agent-src'
HERMES_WORKSPACES='$ROOT/workspaces'
WEBUI_PORT=8787
WEBUI_IMAGE=ghcr.io/nesquena/hermes-webui:latest
ENV
  )
  pass 'Created private ~/Hermes/.env'
else
  say 'Keeping existing ~/Hermes/.env'
fi
if [[ ! -e "$ROOT/compose.yaml" ]]; then
  cp -- "$HERE/docker/compose.yaml" "$ROOT/compose.yaml"
  pass 'Installed Docker Compose configuration'
elif ! cmp -s "$HERE/docker/compose.yaml" "$ROOT/compose.yaml"; then
  backup="$ROOT/compose.yaml.backup.$(date +%Y%m%d-%H%M%S)"
  cp -p -- "$ROOT/compose.yaml" "$backup"
  cp -- "$HERE/docker/compose.yaml" "$ROOT/compose.yaml"
  pass "Backed up previous Compose to $backup"
else
  say 'Keeping unchanged Docker Compose configuration'
fi

if [[ ! -e "$ROOT/agent-src" ]]; then
  say 'Downloading Hermes Agent SOURCE (for Docker; not installed natively)'
  git clone --depth 1 "${HERMES_AGENT_GIT_URL:-https://github.com/NousResearch/hermes-agent.git}" "$ROOT/agent-src"
elif [[ -d "$ROOT/agent-src/.git" ]]; then
  if (( UPDATE_AGENT )); then
    say 'Updating Docker agent source (fast-forward only)'
    [[ -z "$(git -C "$ROOT/agent-src" status --porcelain)" ]] || die 'Agent source checkout has local modifications. Refusing to update.'
    git -C "$ROOT/agent-src" pull --ff-only
  else
    say 'Keeping existing Docker agent source checkout'
  fi
else
  die "$ROOT/agent-src exists but is not a normal Git checkout. Inspect manually."
fi

say 'Preparing isolated Python virtual environment for host sync CLI'
if [[ ! -x "$VENV/bin/python" ]]; then
  python3 -m venv "$VENV" || die 'Could not create Python venv (on Ubuntu: install python3-venv).'
fi
"$VENV/bin/python" -m pip install --disable-pip-version-check -q -r "$HERE/requirements.txt"
# Build a stable launcher with a correct absolute interpreter. Avoid sed/shebang issues.
"$VENV/bin/python" - "$HERE/cli/hermes_sync_v8.py" "$CLI_TARGET" "$VENV/bin/python" <<'PY'
from pathlib import Path
import sys
source, target, interpreter = map(Path, sys.argv[1:4])
lines = source.read_text(encoding="utf-8").splitlines(keepends=True)
assert lines[0].startswith("#!"), "Expected Python shebang"
target.write_text(f"#!{interpreter}\n" + "".join(lines[1:]), encoding="utf-8")
target.chmod(0o755)
PY

backup_launcher() {
  local src="$1" dst
  [[ -e "$src" || -L "$src" ]] || return 0
  [[ "$(readlink -f -- "$src" 2>/dev/null || true)" == "$CLI_TARGET" ]] && return 0
  dst="$CLI_HOME/backups/$(date +%Y%m%d-%H%M%S)-$(basename "$src")"
  mkdir -p "$CLI_HOME/backups"
  # Protect backup against accidental collision if invoked twice in a second.
  while [[ -e "$dst" ]]; do dst="$dst.bak"; done
  mv -- "$src" "$dst"
  warn "Saved previous launcher: $dst"
}
backup_launcher "$BIN_DIR/hermes"
backup_launcher "$BIN_DIR/hermes-sync"
ln -sfn -- "$CLI_TARGET" "$BIN_DIR/hermes"
ln -sfn -- "$CLI_TARGET" "$BIN_DIR/hermes-sync"
pass 'Installed host-side hermes CLI (no native agent)'

if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
  case "${SHELL:-}" in
    */zsh) profile="$HOME/.zshrc";;
    */bash) profile="$HOME/.bashrc";;
    *) profile="$HOME/.profile";;
  esac
  if ! grep -Fq '# hermes portable cli PATH' "$profile" 2>/dev/null; then
    printf '\n# hermes portable cli PATH\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$profile"
    warn "Added ~/.local/bin to $profile. Open a new terminal or run: export PATH=\"\$HOME/.local/bin:\$PATH\""
  fi
fi

if (( NO_START )); then
  pass 'Prepared files without starting Docker (--no-start)'
  exit 0
fi
say 'Validating and starting Hermes Docker WebUI'
(cd "$ROOT" && docker compose config --quiet)
# Do not accidentally hijack a container with same name managed by another project.
if docker inspect hermes-webui >/dev/null 2>&1; then
  origin="$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' hermes-webui 2>/dev/null || true)"
  if [[ -n "$origin" && "$origin" != '<no value>' && "$origin" != "$ROOT" ]]; then
    die "Existing hermes-webui container belongs to $origin, not $ROOT. Stop and review it manually first."
  fi
fi
(cd "$ROOT" && docker compose pull)
"$BIN_DIR/hermes" boot
pass 'Docker services started (first boot can take several minutes to initialize Python dependencies)'
printf '\nOpen WebUI: http://localhost:8787\n'
printf 'Configure your model provider / ChatGPT OAuth in the WebUI (manual approval required).\n'
printf 'Validate: ./doctor.sh\n'
printf 'Project workflow: hermes share --name NAME --yes ; hermes review --gui ; hermes sync\n'
