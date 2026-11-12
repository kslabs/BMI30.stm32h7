"""Build the Bluetooth-card remote-test UI candidate; never alter a release."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DEFAULT_SOURCE = ROOT/'FirmwareReleases/RaspberryPi/2026-10-30-085518/review/bundle/project/hotspot_info_server.py'
DEFAULT_OUTPUT = ROOT/'.tmp/bluetooth-remote-test-20261102/candidate/portal-ui.py'


def once(source, old, new):
    count = source.count(old)
    if count != 1:
        raise ValueError(f'Expected one patch anchor, found {count}: {old[:100]!r}')
    return source.replace(old, new, 1)


def patch(source):
    if 'BMI30_BLUETOOTH_REMOTE_TEST_ASSETS' in source:
        raise ValueError('Remote-test UI is already present')
    script = (HERE/'bluetooth_remote_test.js').read_text(encoding='utf-8')
    css = (HERE/'bluetooth_remote_test.css').read_text(encoding='utf-8')
    assets = '<style id="bmi30-bluetooth-remote-test-style">'+css+'</style>\n<script id="bmi30-bluetooth-remote-test">'+script+'</script>'
    source = once(source, 'def render_portal_page(',
                  'BMI30_BLUETOOTH_REMOTE_TEST_ASSETS = '+repr(assets)+'\n\n\ndef render_portal_page(')
    source = once(source, '  <script>\n    var menuButtons = ',
                  '  {BMI30_BLUETOOTH_REMOTE_TEST_ASSETS}\n  <script>\n    var menuButtons = ')
    source = once(source, '    rssi: int | None = None\n',
        '    hid_capable = bool(set(uuid_codes) & {"1812", "1124"}) or icon.lower().startswith(("input-keyboard", "input-mouse", "input-gaming", "input-tablet"))\n'
        '    rssi: int | None = None\n')
    source = once(source, '        "audio_capable": bool(class_is_audio or uuid_is_audio or icon_is_audio),\n',
        '        "audio_capable": bool(class_is_audio or uuid_is_audio or icon_is_audio),\n        "hid_capable": hid_capable,\n')
    source = once(source, '      function bluetoothProfileLabel(device) {{\n',
        '      function bluetoothProfileLabel(device) {{\n'
        "        if (window.BMI30RemoteTest && window.BMI30RemoteTest.isHid(device)) return window.BMI30RemoteTest.label('hidProfile');\n")
    source = once(source, "      function bluetoothDeviceCard(device) {{\n        var address = String(device.address || '');\n",
        "      function bluetoothDeviceCard(device) {{\n        var address = String(device.address || '');\n"
        "        var hidRemote = !!(window.BMI30RemoteTest && window.BMI30RemoteTest.isHid(device));\n"
        "        var remoteWidget = window.BMI30RemoteTest ? window.BMI30RemoteTest.card(device) : '';\n")
    source = once(source,
        "        var audioType = device.audio_capable ? 'Audio headset/headphones' : (device.audio_known ? 'Non-audio or unsupported' : 'Unknown before service discovery');\n",
        "        var audioType = hidRemote ? window.BMI30RemoteTest.label('controller') : (device.audio_capable ? 'Audio headset/headphones' : (device.audio_known ? 'Non-audio or unsupported' : 'Unknown before service discovery'));\n")
    source = once(source, "        return '<article class=\"bluetooth-device\">' +\n",
        "        return '<article class=\"bluetooth-device' + (remoteWidget ? ' bluetooth-device--remote' : '') + '\"><div class=\"bluetooth-device-main\">' +\n")
    source = once(source,
        "          '<div class=\"bluetooth-device-meta\"><span>Type: <strong>' + escapeBluetoothText(audioType) + '</strong></span><span>Profile: <strong>' + escapeBluetoothText(bluetoothProfileLabel(device)) + '</strong></span></div>' +\n",
        "          '<div class=\"bluetooth-device-meta\"><span>Type: <strong' + (hidRemote ? ' data-bt-hid-label=\"controller\"' : '') + '>' + escapeBluetoothText(audioType) + '</strong></span><span>Profile: <strong' + (hidRemote ? ' data-bt-hid-label=\"hidProfile\"' : '') + '>' + escapeBluetoothText(bluetoothProfileLabel(device)) + '</strong></span></div>' +\n")
    source = once(source, "          '</article>';\n      }}\n\n      function renderBluetoothStatus(data)",
        "          '</div>' + remoteWidget + '</article>';\n      }}\n\n      function renderBluetoothStatus(data)")
    source = once(source,
        "            : '<div class=\"bluetooth-empty\">No devices found yet. Put the headset into pairing mode and press Scan 20 s.</div>';\n",
        "            : '<div class=\"bluetooth-empty\">No devices found yet. Put the headset into pairing mode and press Scan 20 s.</div>';\n"
        "          if (window.BMI30RemoteTest) window.BMI30RemoteTest.refresh();\n")
    ast.parse(source)
    return source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=DEFAULT_SOURCE)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.output.resolve() == args.source.resolve() or 'FirmwareReleases' in args.output.resolve().parts:
        parser.error('Output must be a separate candidate outside immutable releases')
    data = args.source.read_bytes()
    candidate = patch(data.decode('utf-8'))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(candidate, encoding='utf-8', newline='\n')
    report = {'source':str(args.source), 'source_sha256':hashlib.sha256(data).hexdigest(),
              'candidate':str(args.output),'candidate_sha256':hashlib.sha256(args.output.read_bytes()).hexdigest(),
              'scope':'Bluetooth T33 card UI and HID type/profile metadata; HTTP routes are integrated separately'}
    args.output.with_suffix('.build.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
