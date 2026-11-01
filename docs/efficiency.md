# Hermes context efficiency profile


For a sluggish Agent, the tested efficiency profile limits one user request to 90 tool iterations, disables periodic skill-creation nudges (manual/on-demand skill creation remains available), bounds terminal/file output, and enables proactive pruning of bulky *old* tool results. It leaves the chosen model, provider, reasoning level, context-window autodetection, OAuth credentials, project workspaces, and sync engine unchanged.

```bash
# Preview: nothing modified
~/.local/share/hermes-sync/venv/bin/python scripts/tune-efficiency.py \
  --home "$HOME/Hermes/data/.hermes"

# Apply with a timestamped config backup
~/.local/share/hermes-sync/venv/bin/python scripts/tune-efficiency.py \
  --home "$HOME/Hermes/data/.hermes" --apply

# Replace the injected workflow with the focused version (also backs up)
~/.local/share/hermes-sync/venv/bin/python scripts/apply-agent-guidance.py \
  --home "$HOME/Hermes/data/.hermes" --repo "$PWD" --replace-workflow
```

After a task finishes, restart with `hermes stop && hermes boot`, and test in a **new conversation**. Some previously stored giant sessions may still be slow. Full logs can be retained outside the model context; when truncation hides a necessary diagnostic, request a smaller range of that log. The 120K compression ceiling and 48K prune trigger are tunable starting points, not guaranteed improvements for every workload. Running this script is optional on new installations.
