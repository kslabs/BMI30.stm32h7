"""Extend portable assets without invalidating historical schema-2 bundles."""
from pathlib import Path
from patch_access import once

def patch(text, vendor_files):
    extras={
      **{'opt/bmi30-network/'+n:0o644 for n in ('access.py','access_policy.py','access_power.py','Open-BMI30.cmd')},
      **{'opt/bmi30-network/vendor/'+n:0o644 for n in vendor_files},
      'etc/systemd/system/bmi30-access.service':0o644,
      'etc/systemd/logind.conf.d/80-bmi30-access.conf':0o644,
      'etc/systemd/system/bmi30-core.service.d/80-bmi30-offline-start.conf':0o644,
      'etc/NetworkManager/dnsmasq-shared.d/91-bmi30-captive-detection.conf':0o644,
      'usr/local/bin/bmi30-hotspot.sh':0o755,
      'usr/local/sbin/bmi30-wifi-watchdog.py':0o755,
    }
    marker="PROJECT_SOURCES = {"
    text=once(text,marker,'LEGACY_ALLOWLIST = dict(ALLOWLIST)\nACCESS_ASSETS = '+repr(extras)+'\nALLOWLIST.update(ACCESS_ASSETS)\n\n'+marker)
    text=once(text,"        if name.startswith('opt/bmi30-network/'):",
        "        if name in ACCESS_ASSETS:\n            candidates = [system_root / name]\n        elif name.startswith('opt/bmi30-network/'):")
    text=once(text,"json.dumps({'schema': 2, 'assets': found}","json.dumps({'schema': 3, 'assets': found}")
    text=once(text,"record.get('schema') not in (1, 2)","record.get('schema') not in (1, 2, 3)")
    text=once(text,"required = ALLOWLIST.keys() if record['schema'] == 2 else REQUIRED",
        "required = ALLOWLIST.keys() if record['schema'] == 3 else (LEGACY_ALLOWLIST.keys() if record['schema'] == 2 else REQUIRED)")
    text=once(text,"    if record['schema'] == 2:","    if record['schema'] in (2, 3):")
    # Installed files are canonical during capture; do not package stale utility copies.
    text=text.replace("candidates = [project / 'utilities/bmi30_communication' / leaf, system_root / name]",
                      "candidates = [system_root / name, project / 'utilities/bmi30_communication' / leaf]")
    text=text.replace("candidates = [project / 'ops/systemd' / leaf,\n                          project / 'utilities/bmi30_communication' / leaf, system_root / name]",
                      "candidates = [system_root / name, project / 'ops/systemd' / leaf,\n                          project / 'utilities/bmi30_communication' / leaf]")
    text=once(text,"    for unit in units:\n", "    if 'etc/systemd/system/bmi30-access.service' in names:\n        subprocess.run(['systemctl', 'enable', 'bmi30-access.service'], check=True, timeout=15)\n        units += ['bmi30-access.service', 'bmi30-wifi-watchdog.service']\n    for unit in units:\n")
    return text
