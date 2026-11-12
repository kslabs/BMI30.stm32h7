#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
    echo "Run as root: sudo $0" >&2
    exit 1
fi

xrdp_ini="/etc/xrdp/xrdp.ini"
backup="/etc/xrdp/xrdp.ini.codex-backup.$(date +%Y%m%d-%H%M%S)"
tmpfile="$(mktemp /tmp/xrdp.ini.codex.XXXXXX)"

format_copy_bytes() {
    local bytes="${1:-0}"
    [[ "$bytes" =~ ^[0-9]+$ ]] || bytes=0
    numfmt --to=iec --suffix=B "$bytes" 2>/dev/null || printf '%sB' "$bytes"
}

cleanup() {
    rm -f "$tmpfile"
}
trap cleanup EXIT

if [[ ! -f "$xrdp_ini" ]]; then
    echo "Missing $xrdp_ini" >&2
    exit 1
fi

copy_start_ts="$(date +%s)"
cp -a "$xrdp_ini" "$backup"
copy_end_ts="$(date +%s)"
copy_elapsed_s=$((copy_end_ts - copy_start_ts))
if (( copy_elapsed_s <= 0 )); then
    copy_elapsed_s=1
fi
copy_bytes="$(stat -c '%s' "$backup" 2>/dev/null || printf '0')"
[[ "$copy_bytes" =~ ^[0-9]+$ ]] || copy_bytes=0
copy_rate=$((copy_bytes / copy_elapsed_s))
echo "Backup copy: duration ${copy_elapsed_s}s, average speed $(format_copy_bytes "$copy_rate")/s, size $(format_copy_bytes "$copy_bytes")" >&2

awk '
    function emit_xorg_sections() {
        print ""
        print "[Xorg]"
        print "name=Xorg"
        print "lib=libxup.so"
        print "username=ask"
        print "password=ask"
        print "ip=127.0.0.1"
        print "port=-1"
        print "code=20"
        print ""
        print "[Xvnc]"
        print "name=Xvnc"
        print "lib=libvnc.so"
        print "username=ask"
        print "password=ask"
        print "ip=127.0.0.1"
        print "port=-1"
        print "#xserverbpp=24"
        print "#delay_ms=2000"
        print "; Disable requested encodings to support buggy VNC servers"
        print "; (1 = ExtendedDesktopSize)"
        print "#disabled_encodings_mask=0"
        print "; Use this to connect to a chansrv instance created outside of sesman"
        print "; (e.g. as part of an x11vnc console session). Replace '\''0'\'' with the"
        print "; display number of the session"
        print "#chansrvport=DISPLAY(0)"
        xorg_sections_done = 1
    }

    /^\[Globals\][[:space:]]*$/ {
        if (in_globals && !autorun_done) {
            print "autorun=Xorg"
            autorun_done = 1
        }
        if (in_globals && !security_layer_done) {
            print "security_layer=negotiate"
            security_layer_done = 1
        }
        in_globals = 1
        print
        next
    }

    /^\[BMI30_SHARED_DESKTOP\][[:space:]]*$/ {
        if (in_globals && !autorun_done) {
            print "autorun=Xorg"
            autorun_done = 1
        }
        if (in_globals && !security_layer_done) {
            print "security_layer=negotiate"
            security_layer_done = 1
        }
        in_globals = 0
        skip_session = 1
        next
    }

    /^\[(Xorg|Xvnc)\][[:space:]]*$/ {
        if (in_globals && !autorun_done) {
            print "autorun=Xorg"
            autorun_done = 1
        }
        if (in_globals && !security_layer_done) {
            print "security_layer=negotiate"
            security_layer_done = 1
        }
        in_globals = 0
        if (!xorg_sections_done) {
            emit_xorg_sections()
        }
        skip_session = 1
        next
    }

    /^# BMI30_SHARED_DESKTOP_(BEGIN|END)[[:space:]]*$/ {
        next
    }

    /^\[[^]]+\][[:space:]]*$/ {
        if (in_globals && !autorun_done) {
            print "autorun=Xorg"
            autorun_done = 1
        }
        if (in_globals && !security_layer_done) {
            print "security_layer=negotiate"
            security_layer_done = 1
        }
        in_globals = 0
        skip_session = 0
    }

    skip_session {
        next
    }

    in_globals && /^[[:space:]]*autorun=/ {
        print "autorun=Xorg"
        autorun_done = 1
        next
    }

    in_globals && /^[[:space:]]*security_layer=/ {
        print "security_layer=negotiate"
        security_layer_done = 1
        next
    }

    {
        print
    }

    END {
        if (in_globals && !autorun_done) {
            print "autorun=Xorg"
        }
        if (in_globals && !security_layer_done) {
            print "security_layer=negotiate"
        }
        if (!xorg_sections_done) {
            emit_xorg_sections()
        }
    }
' "$xrdp_ini" > "$tmpfile"

copy_start_ts="$(date +%s)"
install -m 644 "$tmpfile" "$xrdp_ini"
copy_end_ts="$(date +%s)"
copy_elapsed_s=$((copy_end_ts - copy_start_ts))
if (( copy_elapsed_s <= 0 )); then
    copy_elapsed_s=1
fi
copy_bytes="$(stat -c '%s' "$xrdp_ini" 2>/dev/null || printf '0')"
[[ "$copy_bytes" =~ ^[0-9]+$ ]] || copy_bytes=0
copy_rate=$((copy_bytes / copy_elapsed_s))
echo "Config install: duration ${copy_elapsed_s}s, average speed $(format_copy_bytes "$copy_rate")/s, size $(format_copy_bytes "$copy_bytes")" >&2

if [[ -e /usr/share/ovscsetup.sh ]]; then
    chmod 755 /usr/share/ovscsetup.sh
fi

if systemctl list-unit-files --type=service 2>/dev/null | grep -q '^bmi30-x11vnc\.service'; then
    systemctl disable --now bmi30-x11vnc.service || true
fi

systemctl restart xrdp-sesman.service xrdp.service

echo "Backup: $backup"
grep -nE '^security_layer=|^autorun=|^\[BMI30_SHARED_DESKTOP\]|^\[Xorg\]|^\[Xvnc\]' "$xrdp_ini"
