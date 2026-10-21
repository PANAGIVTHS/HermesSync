# Testing

All tests operate on disposable directories and must **never** be pointed at
real project folders or the live `~/Hermes` runtime. They check the host
synchronization CLI and install scripts; most Docker behavior is simulated.

## Requirements

Python 3.10+, Git 2.38+, and the dependencies in `requirements.txt`.
On the installed system, you can use the host CLI's Python environment:
`~/.local/share/hermes-sync/venv/bin/python`.

## Run the checks

From the cloned setup repository:

```bash
bash -n bootstrap.sh setup.sh update.sh manage.sh doctor.sh scripts/*.sh
python3 -m py_compile cli/hermes_sync.py scripts/apply-agent-guidance.py
python3 -m unittest discover -s tests -p 'test_gitignore.py' -v
python3 -m unittest discover -s tests -p 'test_agent_guidance.py' -v
python3 -m unittest discover -s tests -p 'test_tools_environment.py' -v
python3 tests/test_hermes_sync.py --sync cli/hermes_sync.py --quick --jobs 4
python3 tests/test_hermes_sync_gui.py --sync cli/hermes_sync.py
python3 tests/test_hermes_cli.py -q
python3 tests/test_hermes_use.py -q
python3 tests/test_hermes_boot.py -q
python3 tests/test_hermes_stop.py -q
bash scripts/test-installer.sh
```

The Git ignore tests cover newly created agent-side `.gitignore` files,
Gradle caches, nested rules, negations, tracked-file preservation, and
original `.git` protection. The GUI tests simulate launching VS Code;
they do not test interactive editor clicks.

## Real machine checks

A genuine fresh-machine deployment is **not** covered by the simulated
Docker tests. After installation, run:

```bash
./doctor.sh
hermes list
hermes boot
```

Verify with `docker inspect hermes-webui` that only the selected project
is mounted under `/workspace`, and that `/opt/hermes-tools` is mounted.
Complete model OAuth separately. Do not paste `auth.json`, `.env`, or
`config.yaml` (which may contain private credentials) into bug reports.
