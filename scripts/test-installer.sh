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
[[ -d "$HOME/Hermes/tools/bin" && -d "$HOME/Hermes/idle-workspace" ]]
[[ -f "$HOME/Hermes/tools/env.sh" && -d "$HOME/Hermes/tools/env.d" && -d "$HOME/Hermes/tools/caches/gradle" ]]
[[ -f "$HOME/Hermes/data/.hermes/SOUL.md" && -f "$HOME/Hermes/data/.hermes/WORKFLOW.md" ]]
grep -q 'BEGIN HERMES PORTABLE WORKFLOW' "$HOME/Hermes/data/.hermes/config.yaml"
grep -q 'BASH_ENV: /opt/hermes-tools/env.sh' "$HOME/Hermes/compose.yaml"
HERMES_TOOLS_HOME="$HOME/Hermes/tools" BASH_ENV="$HOME/Hermes/tools/env.sh" bash -c '[[ "$PATH" == *"$HERMES_TOOLS_HOME/bin"* ]] && [[ "$GRADLE_USER_HOME" == "$HERMES_TOOLS_HOME/caches/gradle" ]]' 
grep -q '^HERMES_ACTIVE_PROJECT=_inactive' "$HOME/Hermes/.env"
grep -q 'source: ${HERMES_ACTIVE_WORKSPACE}' "$HOME/Hermes/compose.yaml"
grep -q 'source: ${HERMES_TOOLS_HOME}' "$HOME/Hermes/compose.yaml"
[[ ! -e "$HOME/.hermes" ]]
"$HOME/.local/bin/hermes" --version | grep -q 'Hermes Sync v9'
[[ "$(readlink -f "$HOME/.local/bin/hermes-sync")" == "$(readlink -f "$HOME/.local/bin/hermes")" ]]
[[ "$(find "$HOME/.local/share/hermes-sync/backups" -type f | wc -l)" -eq 1 ]]
grep -q 'ORIGINAL-HERMES' "$HOME/.local/share/hermes-sync/backups/"*hermes

# Simulate existing authenticated WebUI state: migration must preserve it.
mkdir -p "$HOME/Hermes/data/.hermes"
printf 'TEST-ONLY-TOKEN\n' > "$HOME/Hermes/data/.hermes/auth.json"
printf 'model:\n  provider: test-provider\nagent:\n  system_prompt: "Custom instruction from before migration"\n' > "$HOME/Hermes/data/.hermes/config.yaml"
auth_before="$(sha256sum "$HOME/Hermes/data/.hermes/auth.json" | cut -d' ' -f1)"
config_before="$(sha256sum "$HOME/Hermes/data/.hermes/config.yaml" | cut -d' ' -f1)"

# Idempotency: rerunning installer must preserve .env and user files.
printf '\n# user customization\n' >> "$HOME/Hermes/tools/env.sh"
env_before="$(sha256sum "$HOME/Hermes/tools/env.sh" | cut -d' ' -f1)"
cat > "$HOME/Hermes/workspaces/preserved.txt" <<'TEXT'
DO NOT DELETE ME
TEXT
before="$(sha256sum "$HOME/Hermes/.env" | cut -d' ' -f1)"
"$HERE/setup.sh" --no-start > "$TMP/second.log" 2>&1 || {
  cat "$TMP/second.log" >&2; exit 1;
}
# Installer migrates existing config once, preserving custom provider and prompt.
grep -q 'test-provider' "$HOME/Hermes/data/.hermes/config.yaml"
grep -q 'Custom instruction from before migration' "$HOME/Hermes/data/.hermes/config.yaml"
grep -q 'BEGIN HERMES PORTABLE WORKFLOW' "$HOME/Hermes/data/.hermes/config.yaml"
config_before="$(sha256sum "$HOME/Hermes/data/.hermes/config.yaml" | cut -d' ' -f1)"
[[ "$(sha256sum "$HOME/Hermes/.env" | cut -d' ' -f1)" == "$before" ]]
[[ "$(cat "$HOME/Hermes/workspaces/preserved.txt")" == 'DO NOT DELETE ME' ]]
[[ "$(sha256sum "$HOME/Hermes/data/.hermes/auth.json" | cut -d' ' -f1)" == "$auth_before" ]]
[[ "$(sha256sum "$HOME/Hermes/data/.hermes/config.yaml" | cut -d' ' -f1)" == "$config_before" ]]
[[ "$(sha256sum "$HOME/Hermes/tools/env.sh" | cut -d' ' -f1)" == "$env_before" ]]

# Simulate a pre-Option-B v8 Compose installation and migrate it safely.
sed -i '/^HERMES_ACTIVE_PROJECT=/d; /^HERMES_ACTIVE_WORKSPACE=/d; /^HERMES_TOOLS_HOME=/d' "$HOME/Hermes/.env"
cat > "$HOME/Hermes/compose.yaml" <<'OLD_COMPOSE'
name: hermes
services:
  webui:
    image: ghcr.io/nesquena/hermes-webui:latest
    volumes:
      - type: bind
        source: ${HERMES_WORKSPACES}
        target: /workspace
OLD_COMPOSE
"$HERE/setup.sh" --no-start --replace-compose > "$TMP/migration.log" 2>&1 || {
  cat "$TMP/migration.log" >&2; exit 1;
}
grep -q '^HERMES_ACTIVE_PROJECT=' "$HOME/Hermes/.env"
grep -q 'source: ${HERMES_ACTIVE_WORKSPACE}' "$HOME/Hermes/compose.yaml"
grep -q 'source: ${HERMES_TOOLS_HOME}' "$HOME/Hermes/compose.yaml"
[[ "$(cat "$HOME/Hermes/workspaces/preserved.txt")" == 'DO NOT DELETE ME' ]]
[[ "$(sha256sum "$HOME/Hermes/data/.hermes/auth.json" | cut -d' ' -f1)" == "$auth_before" ]]
[[ "$(sha256sum "$HOME/Hermes/data/.hermes/config.yaml" | cut -d' ' -f1)" == "$config_before" ]]
[[ "$(sha256sum "$HOME/Hermes/tools/env.sh" | cut -d' ' -f1)" == "$env_before" ]]

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
# Selection can be configured with Docker stopped; project and tool files persist.
mkdir -p "$TMP/fakebin"
cat > "$TMP/fakebin/docker" <<'DOCKER_EARLY'
#!/bin/sh
if [ "$1" = inspect ]; then exit 1; fi
exit 0
DOCKER_EARLY
chmod +x "$TMP/fakebin/docker"
PATH="$TMP/fakebin:$PATH" "$HOME/.local/bin/hermes" use T > "$TMP/use.log"
grep -q "HERMES_ACTIVE_PROJECT='T'" "$HOME/Hermes/.env"
PATH="$TMP/fakebin:$PATH" "$HOME/.local/bin/hermes" use --none > "$TMP/unuse.log"
grep -q "HERMES_ACTIVE_PROJECT='_inactive'" "$HOME/Hermes/.env"

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
[[ "$(sha256sum "$HOME/Hermes/data/.hermes/auth.json" | cut -d' ' -f1)" == "$auth_before" ]]
[[ "$(sha256sum "$HOME/Hermes/data/.hermes/config.yaml" | cut -d' ' -f1)" == "$config_before" ]]
[[ "$(sha256sum "$HOME/Hermes/tools/env.sh" | cut -d' ' -f1)" == "$env_before" ]]

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
grep -Eq 'compose .* up -d' "$FAKE_DOCKER_LOG"
# Installed host CLI must also expose a safe, non-destructive stop command.
PATH="$TMP/fakebin:$PATH" "$HOME/.local/bin/hermes" stop > "$TMP/fake-stop.log" 2>&1
grep -Eq 'compose .* stop$' "$FAKE_DOCKER_LOG"
[[ "$(cat "$HOME/Hermes/workspaces/preserved.txt")" == 'DO NOT DELETE ME' ]]
[[ "$(sha256sum "$HOME/Hermes/data/.hermes/auth.json" | cut -d' ' -f1)" == "$auth_before" ]]
[[ "$(sha256sum "$HOME/Hermes/data/.hermes/config.yaml" | cut -d' ' -f1)" == "$config_before" ]]
[[ "$(sha256sum "$HOME/Hermes/tools/env.sh" | cut -d' ' -f1)" == "$env_before" ]]

echo 'PASS: installer isolation, launcher backup, idempotency, Compose backup, CLI merge, Git protection, Docker orchestration' 
