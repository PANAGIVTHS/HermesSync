# Hermes Portable Setup

**Docker-only Hermes Agent + Hermes WebUI**, with a separate host-side `hermes` synchronization CLI (Rich). The sync tool never commits to, stages, or copies your original project's `.git` directory. No native Hermes Agent is installed.

Test target: Linux (Ubuntu/Arch), Git ≥2.38, Docker Engine + Compose v2, Python ≥3.10, Internet for initial downloads. Docker `ghcr.io/nesquena/hermes-webui:latest` image and fresh `NousResearch/hermes-agent` source are fetched at setup time.


## Synchronization and `.gitignore`

The host-side `hermes` CLI synchronizes registered projects using a **separate private Git database**, never your original repository's `.git`. It uses standard Git `.gitignore` rules for **new/untracked** files, including nested ignore files, negations, and newly created agent-side `.gitignore` files. For example, `.gradle/`, `build/`, and other ignored caches remain in the agent workspace without being copied to the desktop.

The `.gitignore` file itself is a project source file and may be synchronized. **Files already included in a previously accepted synchronization checkpoint remain managed**, even if a new `.gitignore` rule matches them; they are not silently deleted or untracked. Explicit Hermes-only `--exclude` rules remain available when sharing a new project.

When Hermes has finished work you haven't synchronized, **do not run `hermes drop`, `hermes purge`, or any cleanup operation** during an update. The normal installer updates the host CLI, templates, and optional container configuration; it does **not** sync or delete project workspaces. See [Updating an existing installation](#updating-an-existing-installation) for backup guidance.

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
  tools/env.sh                    # persistent Bash environment (preserved on reinstall)
  tools/env.d/                    # optional extra per-tool shell environment snippets
  tools/bin/                      # persistent executables, automatically in PATH
  tools/jdks/current -> jdk-25/    # optional active JDK version
  tools/caches/gradle/            # Gradle wrapper distributions and dependency caches
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

## Agent guidance: SOUL.md and workflow rules

The installer configures **two separate global instruction layers**, outside the synchronized project directories:

```text
~/Hermes/data/.hermes/SOUL.md       # Agent identity, tone, and collaboration style
~/Hermes/data/.hermes/WORKFLOW.md   # Persistent operating contract, editable by you
~/Hermes/data/.hermes/config.yaml   # Receives one marked agent.system_prompt block
```

`SOUL.md` is about *how* the agent communicates. The workflow document teaches the Dockerized agent *where it may work* and *how to cooperate with your desktop workflow*: the active workspace, host-only `hermes` commands, shared persistent tools, no Git initialization or commits in copied projects, minimal code changes, automated tests, and review/synchronization left to the user. Project-specific architecture and testing rules belong in the project's existing `AGENTS.md` (or `.hermes.md`) file, not in the global SOUL. No agent-instructions file is silently inserted into a synchronized project.

The installer preserves existing SOUL, WORKFLOW, credentials, and unrelated YAML settings. It only inserts or updates a **clearly marked section** of `agent.system_prompt` using a round-trip YAML parser. Before changing `config.yaml`, it saves a private `.backup.*` file. The installer does not change `approvals.mode`, model, or OAuth configuration.

To intentionally adopt updated repository guidance on an existing installation, use:

```bash
./setup.sh --replace-workflow --no-start   # Backup/update workflow, then refresh config.yaml
./setup.sh --replace-soul --no-start       # OPTIONAL: backup/replace your personalized SOUL
```

After changing global instructions, start a **new WebUI chat/session** so Hermes loads the new context. If you use a `/personality` preset, it can affect how the custom system prompt is used; choose the default personality when validating. To test, ask Hermes which workspace it may modify, where to install a reusable JDK, whether it may run desktop `hermes sync`, and who handles commits. It should answer that it edits the active `/workspace/<project>`, installs reusable tools under `/opt/hermes-tools`, never operates host sync commands, and leaves integration/commits to you. Do not share `config.yaml` or `auth.json` contents when troubleshooting.

If you customize `WORKFLOW.md` directly, rerun `./setup.sh --no-start` to apply its latest contents to the managed config block. Existing guidance is not silently replaced during installer updates; use `--replace-workflow` explicitly to adopt revised upstream templates. For project-specific exceptions (e.g. build a Minecraft mod, but don't launch its game client), keep `AGENTS.md` in the project or explicitly tell Hermes in your task prompt.

**Security note:** These are agent instructions, not a sandbox. Actual per-project filesystem separation still comes from Docker's active-project mount. The agent can access its persistent shared tools and its own runtime state.

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

A **separate persistent global tools area** is mounted at `/opt/hermes-tools` from `~/Hermes/tools/`. The installer **once** sets `BASH_ENV=/opt/hermes-tools/env.sh` in Docker Compose, so Hermes terminal commands running under Bash load that environment automatically, even in noninteractive shells. No further Compose edits are needed for new tools.

- Put standalone binaries or launcher scripts in `/opt/hermes-tools/bin/`; this directory is automatically added to `PATH`.
- The Gradle wrapper manages Gradle itself. Its distributions/dependency cache is persistent at `/opt/hermes-tools/caches/gradle/` via `GRADLE_USER_HOME`.
- Install a JDK under `/opt/hermes-tools/jdks/jdk-25/` (or another version) and make `/opt/hermes-tools/jdks/current` point to it. The environment detects the active JDK and exports `JAVA_HOME` and its `bin` directory on `PATH`. Hermes can change the symlink when a different JDK version is needed.
- For any other tool that requires environment variables, add a **silent, noninteractive, idempotent** file to `/opt/hermes-tools/env.d/tool-name.sh`. The common `env.sh` sources every `*.sh` file from there on every new Bash shell.
- `/opt/hermes-tools/env.sh` is copied from this repository's `tools/env.sh` **only if it does not already exist**. Reinstalling/updating never silently replaces user customizations.

The shared tools folder is outside all registered workspaces and therefore **never appears in `hermes list` or project synchronization**. It persists across project switches, Docker recreation and reboots. Files under it are still genuine host files (this is a bind mount), and tools installed with `apt` inside the container do *not* automatically persist when the container is recreated. Use this shared area for user-space tools; rebuild the image if unavoidable system libraries are required. Python environments may require rebuilding after Python/image upgrades.

**Scope:** `BASH_ENV` is used by Hermes's Bash-based terminal commands, not arbitrary processes that bypass Bash. Newly installed tools are picked up by new Bash processes; a long-lived agent session with a cached environment might need a new session/restart. Shell init snippets execute code, not just set variables: treat anything written into `tools/env.d` as trusted.

You can verify the tools environment in the running container with:

```bash
docker compose -f ~/Hermes/compose.yaml exec webui bash -c 'printf "PATH includes: %s\nGradle cache: %s\n" "$HERMES_TOOLS_HOME/bin" "$GRADLE_USER_HOME"'
```

Project isolation is about ordinary host bind mounts, not an absolute sandbox: agent state, previous chat attachments, and global tools can persist between projects. Do not store other project copies under the shared tools directory. Your original Git repo stays outside Docker. Never mount your Docker socket or entire `~/Hermes/workspaces` into WebUI.

## Updating an existing installation

**Finish or checkpoint any running agent task first.** If Hermes has unmerged work you particularly care about, make an optional backup of its project copy **and** private synchronization state before updating. For example, using the registered project name `MyGPU`:

```bash
mkdir -p "$HOME/Hermes-backups/MyGPU"
cp -a "$HOME/Hermes/workspaces/MyGPU" "$HOME/Hermes-backups/MyGPU/workspace"
cp -a "$HOME/Hermes/sync/projects/MyGPU" "$HOME/Hermes-backups/MyGPU/sync-state"
```

Use a fresh backup destination if you repeat this process; `cp -a` is not an integrity-verification tool. These commands copy the working files and private history **without touching the original repository**. The backup may be large if generated caches exist. Never commit it to this source repository.

Install or update from the **separate cloned setup repository**, not `~/Hermes/`:

```bash
./setup.sh --replace-compose --replace-launcher
```

`--replace-compose` backs up and replaces the Compose template when it differs; `--replace-launcher` backs up any unmanaged host launcher. Existing workspaces, sync state, OAuth credentials, global instructions, and tools are preserved. The installer does not invoke `hermes sync`, `hermes drop`, or `hermes purge`.

For an installation transitioning from an older multi-project container, select the active project after setup with `hermes use PROJECT`; the old all-projects Docker mount is removed when Compose recreates the container. If you use `--no-start`, ensure you recreate the container yourself before trusting the new isolation layout. No re-sharing of registered projects is required.

Afterward, inspect pending changes *before* integrating any agent work:

```bash
hermes list
hermes status --project MyGPU
hermes review --gui --project MyGPU  # optional
# Only when you are satisfied:
hermes sync --project MyGPU
```

If generated caches are still listed, check whether they were already included in an accepted checkpoint, or whether the project's `.gitignore` actually matches them. Don't delete files just to make status look clean.

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

The repository includes regression scripts for the sync CLI. Run them only in temporary directories (the scripts provide their own temp roots) and never point them at `~/Hermes` or real projects. Basic checks: `bash -n bootstrap.sh setup.sh doctor.sh update.sh manage.sh scripts/install-docker.sh`; `python3 -m py_compile cli/hermes_sync.py`; `python3 tests/test_hermes_boot.py`; `python3 tests/test_hermes_stop.py`; `python3 tests/test_hermes_cli.py -q`; `python3 -m unittest tests/test_tools_environment.py`.

This is a setup wrapper around two upstream projects, not a fork or reimplementation of Hermes Agent/WebUI. Check their current licenses and release documentation if redistributing images or modifying upstream code.

## Repository naming and Git history

Keep files under **stable paths** (`cli/hermes_sync.py`, `tests/test_hermes_sync.py`, etc.). Do not put release numbers in filenames. Git commits and optional Git tags express releases; newer versions modify the existing files in place instead of creating a `_v10.py`, `_v11.py`, ... each time.
