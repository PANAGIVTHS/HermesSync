# Hermes Portable Setup

**Docker-only Hermes Agent + Hermes WebUI**, with a separate host-side `hermes` synchronization CLI (Rich). The sync tool never commits to, stages, or copies your original project's `.git` directory. No native Hermes Agent is installed.

Test target: Linux (Ubuntu/Arch), Git ≥2.38, Docker Engine + Compose v2, Python ≥3.10, Internet for initial downloads. Docker `ghcr.io/nesquena/hermes-webui:latest` image and fresh `NousResearch/hermes-agent` source are fetched at setup time.

## Fresh machine (after publishing this folder to your own GitHub repository)

```bash
git clone https://github.com/YOUR-USER/YOUR-REPO.git
cd YOUR-REPO
./bootstrap.sh
```

That one installer sets up the entire app and CLI **if Git, Docker Engine/Compose, and Python are already installed and Docker is accessible without sudo**. If they're missing on Ubuntu or Arch, first run `./scripts/install-docker.sh` interactively, log out and back in if it adds your user to the Docker group, then `./bootstrap.sh`. `bootstrap.sh` can invoke the optional OS installer when dependencies are missing; that helper asks for explicit confirmation before using sudo. A Docker group membership change requires logging out and back in, then rerunning bootstrap. On other distros install Docker manually.

Open **http://localhost:8787**. Authenticate to ChatGPT/Codex in the WebUI (required manual OAuth device authorization; no API key or OAuth token is bundled). Then `./doctor.sh` checks the installation. Start with **`hermes boot`** and stop with **`hermes stop`** (from any directory).

**Never use `sudo ./setup.sh` or `sudo ./bootstrap.sh`**; it would target `/root/Hermes`, not your account.

## Host layout

```text
~/Hermes/                         # RUNTIME DATA; outside your cloned setup repo
  compose.yaml                    # generated from docker/compose.yaml
  .env                            # machine-specific Compose vars (private)
  agent-src/                      # cloned agent source, read-only inside Docker
  data/.hermes/                   # WebUI credentials, config, sessions (PRIVATE)
  workspaces/                     # agent copies, visible at /workspace inside Docker
  sync/projects/                  # private Git history and GUI review state
~/.local/share/hermes-sync/
  venv/                           # Python environment for host CLI, Rich only
  bin/hermes                      # host-side sync CLI, NOT Hermes Agent
~/.local/bin/hermes               # shortcut (may require new shell for PATH)
~/.local/bin/hermes-sync          # compatible shortcut
```

Only scripts, configuration templates, and tests belong in the setup repository. **Do not commit** `~/Hermes/data`, `.env`, `sync`, `workspaces`, `agent-src`, `auth.json`, or copied projects; `~/Hermes/` must remain outside the setup repo. The repo's `.gitignore` excludes common runtime directories as a second line of defense.

## Docker startup

```bash
hermes boot  # Starts Hermes Agent + WebUI; safe to run again when already running
hermes stop  # Stops only the Docker services; leaves containers and files intact
```

This uses `~/Hermes/compose.yaml` (and its `.env`) regardless of your current directory. It runs `docker compose up -d` without pulling new images, re-installing the agent, or touching project workspaces. The initial `./bootstrap.sh` / `./setup.sh` also starts the container via the same command. `hermes stop` runs `docker compose stop` on this installation; it does not delete the container, its configuration, workspaces, credentials, or sync history. Finish any active agent task before stopping it. Open http://localhost:8787 (or your configured `WEBUI_PORT`). Docker Engine/Compose must be available to your user.

## Usage

```bash
cd ~/Projects/MyGPU
hermes share --name MyGPU --exclude 'build/' --exclude '*.log' --yes
# In WebUI choose /workspace/MyGPU, prompt the agent; wait for a checkpoint.
hermes status
hermes review --gui          # Optional VS Code visual review
hermes sync                  # Merge both sides into shared checkpoint
hermes push                  # Send desktop edits to agent (merge, preserve agent edits)
hermes drop                  # Discard agent edits and replace its managed files
hermes undo                  # Undo last sync / push / drop if safe
hermes list
hermes purge --project MyGPU # Permanently deletes ONLY agent copy + private sync history
```

**GUI review requires VS Code's `code` command on the host.** The review uses a scratch Git repo under `~/Hermes/sync/projects/<name>`; your project's actual `.git` is untouched.

## Management and updating

```bash
./manage.sh ps        # Container status
./manage.sh logs      # Follow logs; Ctrl+C only stops following
./manage.sh stop      # Stops container, preserves files
./manage.sh start
./doctor.sh
./update.sh            # Refresh agent source, WebUI image, host CLI and Compose template
```

Updating fetches the current upstream agent and WebUI; if a breaking upstream change occurs, inspect logs. For truly reproducible versions, set a pinned image tag in `~/Hermes/.env` and checkout a pinned agent source ref under `~/Hermes/agent-src` (disable update-agent during maintenance). Version compatibility is controlled upstream and not guaranteed across arbitrary combinations.

### Existing installation precautions

- Setup is re-runnable. It preserves existing credentials, workspaces, and private sync history.
- An existing differing `~/Hermes/compose.yaml` stops setup; **`./setup.sh --replace-compose`** backs it up before replacing it.
- An existing `~/.local/bin/hermes` (for example, a native Hermes Agent launcher) stops setup. **`./setup.sh --replace-launcher`** backs up the launcher; **it does not remove or uninstall any native Agent files.** This avoids damaging an older installation. Inspect the old installation separately if you want it gone.
- Setup never deletes `~/.hermes`, existing source repositories, or your original `.git`.
- Configuration secrets are per-machine; this repository cannot make ChatGPT OAuth approval automatic.
- To prevent unexpected exposure, WebUI binds to **127.0.0.1** only, not LAN/public interfaces. It is still a powerful coding agent with access to the mounted workspaces.
- Docker group access grants effectively root-level power on the host; use only on trusted machines.
- Replacing a Docker setup with conflicting containers/ports must be handled explicitly; setup will not silently hijack a container belonging to another Compose project.

## Troubleshooting

`./doctor.sh` provides quick checks. Docker logs: `cd ~/Hermes && docker compose logs --tail=100`. On a new installation first boot may take several minutes to create the container Python 3.14 virtual environment. Agent files are mounted read-only into Docker; OAuth credentials are persisted in `~/Hermes/data/.hermes/`.

If `hermes` isn't found immediately after install, open a new terminal or run `export PATH="$HOME/.local/bin:$PATH"`. If `./setup.sh` finds an existing native `hermes` command in `~/.local/bin`, you must explicitly approve launcher replacement.

## Tests

The repository includes regression scripts from sync v8. Run them only in temporary directories (the scripts provide their own temp roots) and never point them at `~/Hermes` or real projects. Basic checks: `bash -n bootstrap.sh setup.sh doctor.sh update.sh manage.sh scripts/install-docker.sh`; `python3 -m py_compile cli/hermes_sync_v8.py`; `python3 tests/test_hermes_boot.py`; `python3 tests/test_hermes_stop.py`; `python3 tests/test_hermes_v8.py -q`.

This is a setup wrapper around two upstream projects, not a fork or reimplementation of Hermes Agent/WebUI. Check their current licenses and release documentation if redistributing images or modifying upstream code.
