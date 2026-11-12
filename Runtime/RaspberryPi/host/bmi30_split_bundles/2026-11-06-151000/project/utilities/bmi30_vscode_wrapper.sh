#!/bin/sh

preflight="/home/techaid/Documents/utilities/bmi30_codex_preflight.sh"

if [ -x "$preflight" ]; then
    if command -v timeout >/dev/null 2>&1; then
        timeout 120 "$preflight" || true
    else
        "$preflight" || true
    fi
fi

exec /usr/share/code/bin/code --locale=ru --password-store=basic "$@"
