#!/usr/bin/env bash
# Start the badge dev UI (http://127.0.0.1:8042). Extra args go to server.py, e.g. --project ../probe
set -euo pipefail
IDF_EXPORT="${IDF_EXPORT:-$HOME/esp/esp-idf-v5.4.2/export.sh}"
# shellcheck disable=SC1090
source "$IDF_EXPORT" >/dev/null
cd "$(dirname "$0")"
exec python server.py "$@"
