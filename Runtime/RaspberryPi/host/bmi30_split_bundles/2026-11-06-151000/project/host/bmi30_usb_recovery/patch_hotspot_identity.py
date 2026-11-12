"""Identify our AP by its mode/interface, never by a neighbour's SSID."""
import ast

BODY='''def _is_hotspot_connection(connection_id: str, device: str = "") -> bool:
    if device.lower() == "wlan0ap":
        return True
    mode = run_command("nmcli", "-g", "802-11-wireless.mode", "connection", "show", connection_id).strip().lower()
    iface = run_command("nmcli", "-g", "connection.interface-name", "connection", "show", connection_id).strip().lower()
    return mode == "ap" or iface == "wlan0ap"
'''

def portal(source):
    tree=ast.parse(source)
    nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_is_hotspot_connection']
    if len(nodes)!=1:raise ValueError('Unknown portal hotspot detector')
    node=nodes[0];lines=source.splitlines(keepends=True)
    return ''.join(lines[:node.lineno-1])+BODY+''.join(lines[node.end_lineno:])

def identity_script(source):
    """The boot identity refresh must never rename an infrastructure peer profile."""
    old='if [[ "$mode" == "ap" || "$ipv4_method" == "shared" || "$current_if" == "wlan0ap" || "$lowered_name" == hotspot* || "$lowered_id" == hotspot* || "$lowered_ssid" == bmi30* ]]; then'
    new='if [[ "$mode" == "ap" || "$current_if" == "wlan0ap" ]]; then'
    if old in source:
        if source.count(old)!=1:raise ValueError('Unknown network identity profile selector')
        source=source.replace(old,new,1)
    elif new not in source:raise ValueError('Unknown network identity profile selector')
    source=source.replace('[[ "$mode" == "ap" || "$ipv4_method" == "shared" ]] || continue',
                          '[[ "$mode" == "ap" ]] || continue')
    source=source.replace('nmcli connection up "$ssid" >/dev/null 2>&1 || nmcli connection up "$active_uuid" >/dev/null 2>&1 || true',
                          'nmcli connection up "$active_uuid" >/dev/null 2>&1 || true')
    return source


def boot_script(source):
    # Client profiles named BMI30-* belong to adjacent devices. They must not
    # be converted into our AP, renamed, or deleted during AP installation.
    old='"$current_if" == "$WLAN_AP" || "$current_mode" == "ap" || "$lowered_name" == hotspot-bmi30.* || "$current_ssid" == BMI30.* || "$current_ssid" == BMI30-*'
    new='"$current_if" == "$WLAN_AP" || "$current_mode" == "ap"'
    if old in source:
        if source.count(old)!=1:raise ValueError('Unknown hotspot setup profile selector')
        source=source.replace(old,new,1)
    elif new not in source:raise ValueError('Unknown hotspot setup profile selector')
    old_name='if [[ "$connection_name" == "$CON" ]]; then'
    new_name='if [[ "$connection_name" == "$CON" && ( "$current_if" == "$WLAN_AP" || "$current_mode" == "ap" ) ]]; then'
    if old_name in source:source=source.replace(old_name,new_name,1)
    if '# BMI30_KEEP_STA_CHANNEL' not in source:
        anchor='    AP_BAND="bg"\n    AP_CHAN="6"\n'
        if anchor not in source:raise ValueError('Unknown hotspot channel detector')
        source=source.replace(anchor,anchor+'    STA_WAS_CONNECTED=0\n',1)
        anchor='    sta_chan="$(nmcli -t -f ACTIVE,CHAN dev wifi'
        if source.count(anchor)!=1:raise ValueError('Unknown hotspot station-channel lookup')
        source=source.replace(anchor,'    STA_WAS_CONNECTED=1\n\n'+anchor,1)
        anchor='    if [[ "$AP_BAND" == "bg" && "$AP_CHAN" == "6" ]]; then'
        if source.count(anchor)!=1:raise ValueError('Unknown hotspot fallback')
        source=source.replace(anchor,'''    # BMI30_KEEP_STA_CHANNEL
    if [[ "${STA_WAS_CONNECTED:-0}" == "1" ]]; then
        echo "AP activation failed; preserving the station channel" >&2
        return 1
    fi

'''+anchor,1)
    if '# BMI30_AP_ACTIVATE_BY_UUID' not in source:
        old='''    if nmcli con up "$CON" >/dev/null 2>&1; then
        return 0
    fi
'''
        new='''    # BMI30_AP_ACTIVATE_BY_UUID
    local ap_attempt
    for ap_attempt in 1 2 3; do
        if nmcli --wait 10 con up uuid "$1" ifname "$WLAN_AP" >/dev/null 2>&1; then
            return 0
        fi
        sleep 2
    done
'''
        if source.count(old)!=1:raise ValueError('Unknown AP activation')
        source=source.replace(old,new,1)
        source=source.replace('    nmcli con up "$CON" >/dev/null\n','    nmcli --wait 10 con up uuid "$1" ifname "$WLAN_AP" >/dev/null\n',1)
        old='''    nmcli con add type wifi ifname "$WLAN_AP" con-name "$CON" ssid "$SSID"
    preserve_existing_password "$CON"
    apply_connection_settings "$CON"
'''
        new='''    target_uuid="$(cat /proc/sys/kernel/random/uuid)"
    nmcli con add type wifi ifname "$WLAN_AP" con-name "$CON" ssid "$SSID" connection.uuid "$target_uuid"
    preserve_existing_password "$target_uuid"
    apply_connection_settings "$target_uuid"
'''
        if source.count(old)!=1:raise ValueError('Unknown AP profile creation')
        source=source.replace(old,new,1)
    return source
