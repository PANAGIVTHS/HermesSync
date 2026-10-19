#!/usr/bin/env bash
# EXPLICIT, interactive OS provisioning helper. This is never run by setup.sh.
# Docker group membership grants root-equivalent host access; user must consent.
set -Eeuo pipefail
[[ "$(uname -s)" == Linux ]] || { echo 'Linux only' >&2; exit 1; }
[[ "$(id -u)" != 0 ]] || { echo 'Run as a normal user (sudo is invoked explicitly).' >&2; exit 1; }
[[ -t 0 ]] || { echo 'Run in an interactive terminal.' >&2; exit 1; }
if [[ -e /etc/os-release ]]; then . /etc/os-release; else echo 'Cannot determine distribution' >&2; exit 1; fi
printf 'This will install Docker and developer prerequisites on %s.\n' "$PRETTY_NAME"
printf 'Granting docker-group access is effectively granting root privileges to your user.\n'
read -r -p 'Type INSTALL to proceed: ' answer
[[ "$answer" == INSTALL ]] || { echo 'Cancelled'; exit 1; }
if [[ "${ID:-}" == ubuntu ]]; then
  sudo apt-get update
  sudo apt-get install -y ca-certificates curl git python3 python3-venv
  sudo install -m 0755 -d /etc/apt/keyrings
  sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  sudo chmod a+r /etc/apt/keyrings/docker.asc
  : "${UBUNTU_CODENAME:=${VERSION_CODENAME:?}}"
  arch="$(dpkg --print-architecture)"
  printf 'Types: deb\nURIs: https://download.docker.com/linux/ubuntu\nSuites: %s\nComponents: stable\nArchitectures: %s\nSigned-By: /etc/apt/keyrings/docker.asc\n' \
      "$UBUNTU_CODENAME" "$arch" | sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null
  sudo apt-get update
  sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
elif [[ "${ID:-}" == arch ]]; then
  sudo pacman -Syu --needed --noconfirm docker docker-compose git python python-pip curl
else
  echo 'Unsupported OS for automatic Docker install. Use your distro official instructions.' >&2
  exit 1
fi
sudo systemctl enable --now docker
if ! id -nG | grep -qw docker; then
  sudo usermod -aG docker "$(id -un)"
  printf '\nAdded your user to docker group. LOG OUT AND LOG BACK IN before running ./setup.sh.\n'
else
  printf '\nDocker group already present. Run ./setup.sh.\n'
fi
