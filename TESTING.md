# Verification notes

All tests below use disposable project directories. These checks were performed
for the stable-filename portable setup and persistent shared-tools environment.

| Test | Result |
|---|---:|
| Core synchronization regression (quick, two batches) | 71/71 passed |
| VS Code GUI proposal regression (simulated launcher) | 8/8 passed |
| CLI/project and purge regression | 11/11 passed |
| Docker boot simulations | 5/5 passed |
| Docker stop simulations | 6/6 passed |
| Active-project isolation / switching simulations | 8/8 passed |
| Persistent tools environment (Bash -c / -lc, Gradle, JDK, env.d) | 7/7 passed |
| Isolated portable installer with dummy agent Git checkout | Passed |
| Shell syntax / Python compilation | Passed |

Total: **116/116 automated test cases passed**, plus the installer integration test.

These tests do not launch a live Docker daemon or download the actual Hermes WebUI
image. On a real machine, verify `hermes boot`, then run `./doctor.sh`, inspect
Docker mounts and ask Hermes to run `command -v my-tool` after installing a test
executable under `/opt/hermes-tools/bin`. Authentication cannot be tested without
the user's approval.

The tool environment itself is tested by actually launching Bash in noninteractive
and login modes with `BASH_ENV` set. This validates shell semantics but not every
upstream version's terminal startup behavior.

CLI tests:

```bash
python3 -m unittest discover -s tests -p 'test_tools_environment.py' -v
python3 tests/test_hermes_sync_gui.py --sync cli/hermes_sync.py
python3 tests/test_hermes_sync.py --sync cli/hermes_sync.py --quick --jobs 4
bash scripts/test-installer.sh
```
