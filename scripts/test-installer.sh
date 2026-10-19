#!/usr/bin/env bash
# Offline-safe integration test: isolated HOME, local dummy agent git source,
# no Docker, no modifications to the actual user's ~/.local or ~/Hermes.
set -Eeuo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
TMP="$(mktemp -d -t hermes-installer-test.XXXXXXXX)"
trap 'rm -rf -- "$TMP"' EXIT
export HOME="$TMP/home"
mkdir -p "$HOME" "$TMP/dummy-agent" "$TMP/source"
# Ensure no inherited user CLI and give our virtual env access to preinstalled Rich.
mkdir -p "$HOME/.local/share/hermes-sync"
python3 -m venv --system-site-packages "$HOME/.local/share/hermes-sync/venv"
# If Rich is already present in the host test interpreter, use it offline.
# Otherwise, exercise the normal pip download in the isolated venv.
if python3 - <<'CHECK' >/dev/null 2>&1
import importlib.metadata
version = importlib.metadata.version('rich')
major = int(version.split('.')[0])
assert 13 <= major < 16
CHECK
then
  export PYTHONPATH="$(python3 -c 'import rich,pathlib; print(pathlib.Path(rich.__file__).resolve().parent.parent)')"
  export PIP_NO_INDEX=1
fi
printf '[build-system]\nrequires=["setuptools"]\nbuild-backend="setuptools.build_meta"\n' > "$TMP/dummy-agent/pyproject.toml"
git -C "$TMP/dummy-agent" init -q
git -C "$TMP/dummy-agent" add .
git -C "$TMP/dummy-agent" -c user.name=Test -c user.email=test@example.invalid commit -qm initial
export HERMES_AGENT_GIT_URL="$TMP/dummy-agent"
# Pre-existing launcher must NOT be overwritten by default.
mkdir -p "$HOME/.local/bin"
printf '#!/bin/sh\necho ORIGINAL-HERMES\n' > "$HOME/.local/bin/hermes"
chmod +x "$HOME/.local/bin/hermes"
if "$HERE/setup.sh" --no-start > "$TMP/refusal.log" 2>&1; then
  echo 'FAIL: overwrote existing hermes launcher without consent' >&2; exit 1
fi
grep -q 'ORIGINAL-HERMES' "$HOME/.local/bin/hermes" || { echo 'FAIL: launcher changed during refusal'; exit 1; }
"$HERE/setup.sh" --no-start --replace-launcher > "$TMP/install.log" 2>&1 || {
  cat "$TMP/install.log" >&2; exit 1;
}
[[ -f "$HOME/Hermes/compose.yaml" && -f "$HOME/Hermes/.env" && -d "$HOME/Hermes/agent-src/.git" ]]
[[ -d "$HOME/Hermes/data/.hermes" && -d "$HOME/Hermes/workspaces" && -d "$HOME/Hermes/sync/projects" ]]
[[ ! -e "$HOME/.hermes" ]]
"$HOME/.local/bin/hermes" --version | grep -q 'Hermes Sync v7'
[[ "$(readlink -f "$HOME/.local/bin/hermes-sync")" == "$(readlink -f "$HOME/.local/bin/hermes")" ]]
[[ "$(find "$HOME/.local/share/hermes-sync/backups" -type f | wc -l)" -eq 1 ]]
grep -q 'ORIGINAL-HERMES' "$HOME/.local/share/hermes-sync/backups/"*hermes

# Idempotency: rerunning installer must preserve .env and user files.
cat > "$HOME/Hermes/workspaces/preserved.txt" <<'TEXT'
DO NOT DELETE ME
TEXT
before="$(sha256sum "$HOME/Hermes/.env" | cut -d' ' -f1)"
"$HERE/setup.sh" --no-start > "$TMP/second.log" 2>&1 || {
  cat "$TMP/second.log" >&2; exit 1;
}
[[ "$(sha256sum "$HOME/Hermes/.env" | cut -d' ' -f1)" == "$before" ]]
[[ "$(cat "$HOME/Hermes/workspaces/preserved.txt")" == 'DO NOT DELETE ME' ]]

# Git isolation and end-to-end sync using the installed CLI.
cd "$TMP/source"
mkdir -p .git
printf 'ORIGINAL GIT MARKER\n' > .git/sentinel
printf 'base\n' > code.sv
"$HOME/.local/bin/hermes" share --name T --yes > "$TMP/share.log"
[[ ! -e "$HOME/Hermes/workspaces/T/.git" ]]
printf 'agent edit\n' >> "$HOME/Hermes/workspaces/T/code.sv"
"$HOME/.local/bin/hermes" sync > "$TMP/sync.log"
[[ "$(cat code.sv)" == $'base\nagent edit' ]]
[[ "$(cat .git/sentinel)" == 'ORIGINAL GIT MARKER' ]]

# Protect differing Compose, unless backed up explicitly.
printf '# local custom compose\n' > "$HOME/Hermes/compose.yaml"
if "$HERE/setup.sh" --no-start > "$TMP/compose-refusal.log" 2>&1; then
  echo 'FAIL: overwrote customized Compose without consent' >&2; exit 1
fi
[[ "$(cat "$HOME/Hermes/compose.yaml")" == '# local custom compose' ]]
"$HERE/setup.sh" --no-start --replace-compose > "$TMP/replace-compose.log" 2>&1 || {
  cat "$TMP/replace-compose.log" >&2; exit 1;
}
[[ -n "$(find "$HOME/Hermes" -name 'compose.yaml.backup.*' -type f -print -quit)" ]]
[[ "$(cat "$HOME/Hermes/workspaces/preserved.txt")" == 'DO NOT DELETE ME' ]]

# Simulate startup command orchestration without a real Docker daemon.
mkdir -p "$TMP/fakebin"
cat > "$TMP/fakebin/docker" <<'DOCKER'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$FAKE_DOCKER_LOG"
case "${1:-}" in
  inspect) exit 1;; # No previously managed container.
  info) exit 0;;
  compose) exit 0;;
  *) exit 1;;
esac
DOCKER
chmod +x "$TMP/fakebin/docker"
export FAKE_DOCKER_LOG="$TMP/docker-invocations.log"
PATH="$TMP/fakebin:$PATH" "$HERE/setup.sh" > "$TMP/fake-docker-setup.log" 2>&1 || {
  cat "$TMP/fake-docker-setup.log" >&2; exit 1;
}
grep -q 'compose config --quiet' "$FAKE_DOCKER_LOG"
grep -q 'compose pull' "$FAKE_DOCKER_LOG"
grep -q 'compose up -d' "$FAKE_DOCKER_LOG"

echo 'PASS: installer isolation, launcher backup, idempotency, Compose backup, CLI merge, Git protection, Docker orchestration' 
