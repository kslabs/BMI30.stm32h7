#!/usr/bin/python3
"""Install EN/RU login/reconnect fixes, including into an offline system copy."""
import argparse
from datetime import datetime
import os
from pathlib import Path
import re
import shutil

PROJECT = Path(__file__).resolve().parents[1]
HOOK = '''# BEGIN BMI30 EN/RU reconnect
# xorgxrdp reloads the client map when reconnecting. Restore toggle options
# after its connection setup; the driver map itself also contains us,ru.
sleep 2
/usr/local/bin/bmi30-keyboard-apply.sh
# END BMI30 EN/RU reconnect
'''


def install(root):
    root = root.resolve()
    helper = root / 'usr/local/bin/bmi30-keyboard-apply.sh'
    keyboard = root / 'etc/xrdp/xrdp_keyboard.ini'
    reconnect = root / 'etc/xrdp/reconnectwm.sh'
    startup = root / 'home/techaid/.local/bin/session-keyboard-init'
    # Validate every input before changing anything. Keep other configuration
    # and keyboard mappings intact (including numeric RDP layout IDs).
    mapping, count = re.subn(
        r'(?m)^(rdp_layout_(?:us|ru)\s*=\s*)(?:us|ru|us,ru)[ \t]*$',
        r'\g<1>us,ru', keyboard.read_text())
    if count < 2:
        raise ValueError('US/RU X11 mappings not found in xrdp_keyboard.ini')
    init = startup.read_text()
    if '/usr/local/bin/bmi30-keyboard-apply.sh' not in init:
        init, count = re.subn(
            r"(?m)^setxkbmap -option ''[^\n]*\nsetxkbmap -model pc105[^\n]*$",
            '/usr/local/bin/bmi30-keyboard-apply.sh', init)
        if count != 1:
            raise ValueError('Unexpected session-keyboard-init; refusing to overwrite')
    hook = reconnect.read_text()
    if '# BEGIN BMI30 EN/RU reconnect' not in hook:
        hook = hook.rstrip() + '\n\n' + HOOK
    files = {helper: (PROJECT / 'ops/scripts/bmi30-keyboard-apply.sh').read_bytes(),
             keyboard: mapping.encode(), reconnect: hook.encode(), startup: init.encode()}
    backup = root / ('var/backups/bmi30-keyboard-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    for path, data in files.items():
        if path.exists() and path.read_bytes() == data:
            continue
        if path.exists():
            dest = backup / path.relative_to(root)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        if path != keyboard:
            path.chmod(0o755)
        print('Updated:', path)
    print('Backup directory (if changes were needed):', backup)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/'),
                        help='Mounted root of the target OS; default is the live OS')
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Run as root (sudo)')
    install(args.root)
