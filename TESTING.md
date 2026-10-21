# Verification notes (Hermes Sync v9)

- Shell syntax (`bash -n` for setup/management scripts): **PASS**.
- Python syntax (`py_compile`): **PASS**.
- `python3 tests/test_hermes_stop.py`: **6/6 passed**, with simulated Docker and disposable workspaces. Ensures stop only calls Compose stop, is repeatable, preserves files, rejects foreign containers, and reports errors without changing the version.
- `python3 tests/test_hermes_boot.py`: **5/5 passed**, using a fake Docker executable and isolated disposable projects. Covers launching from an unrelated current directory, re-running `boot`, missing runtime, Docker failure, and another Compose project's existing container.
- `python3 tests/test_hermes_v9.py -q`: **11/11 passed** (project CLI commands, isolation, purge safety).
- `bash scripts/test-installer.sh` as a non-root user: **PASS** with disposable HOME, dummy local agent Git checkout, and simulated Docker. Confirms installer launches the app via `hermes boot`, backs up launchers, preserves existing data, protects Compose configuration, and does not touch the source `.git`.
- One preexisting VS Code GUI test (`test_01_edit_proposal_pull_and_undo`) passed; an attempt to run the entire GUI suite was **inconclusive (execution timed out)**. The GUI/merge implementation is unchanged from v7; no claim of a full GUI suite pass is made for this build.

No real Docker daemon was run in this environment. The `boot` and `stop` commands was verified through a simulated Docker executable. Live Compose startup, WebUI health, and ChatGPT OAuth still require verification on the target machine. The upstream agent/image versions may change between installs because the installer defaults to upstream Git and `latest`.

## Active project mounting

`python3 tests/test_hermes_use.py` uses simulated Docker to check project changes, no-project selection, rejection of symlinks, purge safety, rollback after Docker failure, and preservation of global tools. This does not replace a real container mount inspection. On a real installation use `docker inspect hermes-webui --format '{{range .Mounts}}{{println .Source "->" .Destination}}{{end}}'` to verify only one path under `/workspace` is mounted.
