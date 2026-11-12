#!/bin/bash
set -euo pipefail

if ((EUID != 0)); then
    echo "Run with sudo/root." >&2
    exit 1
fi

REMOVE_IDENTITY=0
while (($#)); do
    case "$1" in
        --remove-identity)
            REMOVE_IDENTITY=1
            shift
            ;;
        -h|--help)
            cat <<'EOF'
Usage: sudo ./uninstall_bmi30_agent.sh [--remove-identity]

By default /etc/bmi30-agent and /var/lib/bmi30-agent are preserved so the
device keeps its SSH key, API token, pinned host key, and DEVICE_ID.
EOF
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            exit 2
            ;;
    esac
done

systemctl disable --now bmi30-agent.service >/dev/null 2>&1 || true
systemctl disable --now bmi30-tunnel.service >/dev/null 2>&1 || true
rm -f /etc/systemd/system/bmi30-agent.service
rm -f /etc/systemd/system/bmi30-tunnel.service
rm -f /usr/local/sbin/bmi30-agent-ctl
rm -rf /opt/bmi30-agent

if ((REMOVE_IDENTITY == 1)); then
    rm -rf /etc/bmi30-agent /var/lib/bmi30-agent
fi

systemctl daemon-reload
systemctl reset-failed bmi30-agent.service bmi30-tunnel.service >/dev/null 2>&1 || true

echo "BMI30 Agent removed."
if ((REMOVE_IDENTITY == 0)); then
    echo "BMI30 identity and production SSH host pin were preserved."
fi
