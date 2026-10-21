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
  workspaces/                     # independent project copies; only active one mounted
  idle-workspace/                 # empty placeholder when no project selected
  tools/bin/                      # persistent executables, global to all projects
  tools/downloads/                # reusable archives and downloads
  tools/python-envs/              # persistent per-tool virtual environments
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
hermes use MyGPU             # Exposes ONLY this project inside Docker
# In WebUI choose /workspace/MyGPU, prompt the agent; wait for a checkpoint.
hermes status
hermes review --gui          # Optional VS Code visual review
hermes sync                  # Merge both sides into shared checkpoint
hermes push                  # Send desktop edits to agent (merge, preserve agent edits)
hermes drop                  # Discard agent edits and replace its managed files
hermes undo                  # Undo last sync / push / drop if safe
hermes list
hermes use --none           # Unmount the current project before purging it
hermes purge --project MyGPU # Permanently deletes ONLY agent copy + private sync history
```

**GUI review requires VS Code's `code` command on the host.** The review uses a scratch Git repo under `~/Hermes/sync/projects/<name>`; your project's actual `.git` is untouched.

## Per-project isolation and shared tools (Option B)

**A WebUI workspace selection alone is NOT a security boundary.** This setup uses a Docker bind mount so that at most **one registered project** is visible under `/workspace` inside the container. Switching is deliberate and stops active agent work if the container must be recreated. Use it **only between tasks**:

```bash
hermes list                # Show registered projects
hermes use MyGPU           # Confirm to recreate running container
hermes use MinecraftMod    # Replace MyGPU mount with MinecraftMod
hermes use --none          # No project visible inside the container
```

New installs start with `/workspace/_inactive` (an empty placeholder) until `hermes use PROJECT` is run. For a stopped container, `hermes use` changes the next-start selection without starting Docker; run `hermes boot` afterward. WebUI's workspace dropdown should point to `/workspace/PROJECT` after switching; **older saved chat sessions may retain the previous workspace path** and should not be reused for the new project. Do not choose `/workspace` to work across repositories: only the mounted active project is available.

A **separate persistent global tools area** is mounted at `/opt/hermes-tools` from `~/Hermes/tools/`. The agent can reuse downloaded binaries, source archives, Python virtual environments, and scripts for every project, e.g. install scripts into `/opt/hermes-tools/bin/`, then run `/opt/hermes-tools/bin/my-tool`. For shell sessions needing short tool names, use `export PATH="/opt/hermes-tools/bin:$PATH"`. This folder is intentionally **outside** the project sync/list registry, so `hermes sync`, `push`, `drop` and `list` never treat it as a synchronized project. Tool installs via `apt` change the container's writable layer and may not survive Docker recreation; put reusable binaries and environment data in `/opt/hermes-tools` or build a custom image for system packages. Python environments may require rebuilding if the underlying image/Python changes.

Project isolation is about ordinary host bind mounts, not an absolute sandbox: agent state, previous chat attachments, and global tools can persist between projects. Do not store other project copies under the shared tools directory. Your original Git repo stays outside Docker. Never mount your Docker socket or entire `~/Hermes/workspaces` into WebUI.

**Existing v8 installations:** stop active tasks, extract this repository **outside `~/Hermes/`**, then run `./setup.sh --replace-compose --replace-launcher` to back up/reinstall the CLI and switch Compose to single-project mounting. The installer preserves workspaces, sync databases, credentials, and WebUI sessions; initially no project is active. After it starts Docker, run `hermes use PROJECT`. You do not need to re-share existing projects. Before updating manually customized Compose files, review the backed-up configuration. Use `./setup.sh --replace-compose --replace-launcher --no-start` only if you intend to start/recreate Docker separately: **until the running old container is recreated, it may still have access to all workspaces**.

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

The repository includes regression scripts from sync v9. Run them only in temporary directories (the scripts provide their own temp roots) and never point them at `~/Hermes` or real projects. Basic checks: `bash -n bootstrap.sh setup.sh doctor.sh update.sh manage.sh scripts/install-docker.sh`; `python3 -m py_compile cli/hermes_sync_v9.py`; `python3 tests/test_hermes_boot.py`; `python3 tests/test_hermes_stop.py`; `python3 tests/test_hermes_v9.py -q`.

This is a setup wrapper around two upstream projects, not a fork or reimplementation of Hermes Agent/WebUI. Check their current licenses and release documentation if redistributing images or modifying upstream code.
