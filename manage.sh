#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$HOME/Hermes"
[[ -f "$ROOT/compose.yaml" ]] || { echo 'Hermes runtime not installed. Run ./setup.sh first.' >&2; exit 1; }
case "${1:-}" in
  start) (cd "$ROOT" && docker compose up -d);;
  stop) (cd "$ROOT" && docker compose stop);;
  ps|status) (cd "$ROOT" && docker compose ps);;
  logs) (cd "$ROOT" && docker compose logs --tail=100 -f);;
  restart) (cd "$ROOT" && docker compose restart);;
  *) echo 'Usage: ./manage.sh {start|stop|restart|ps|status|logs}' >&2; exit 2;;
esac
