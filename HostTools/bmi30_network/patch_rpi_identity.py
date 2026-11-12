"""Use Raspberry Pi hardware identity throughout an explicitly supplied portal.

Existing release archives are immutable. Apply this patch to the selected
portal source when building/upgrading a release; it does not touch settings,
NetworkManager, STM32 firmware or acquisition code.
"""
from __future__ import annotations

import argparse
import ast
from pathlib import Path

HERE = Path(__file__).resolve().parent
PATCH_MARKER = '# BMI30 hardware RPI identity, 2026-10-23'

HARDWARE = '''# BMI30 hardware RPI identity, 2026-10-23
RPI_IDENTITY_VERSION = "2026-10-23"
_BMI30_HARDWARE_SERIAL = ""


def _bmi30_hardware_serial() -> str:
    """Read the current board, never a cloned hostname or removable medium."""
    global _BMI30_HARDWARE_SERIAL
    if _BMI30_HARDWARE_SERIAL:
        return _BMI30_HARDWARE_SERIAL
    for path in ("/proc/device-tree/serial-number",
                 "/sys/firmware/devicetree/base/serial-number", "/proc/cpuinfo"):
        try:
            with open(path, "r", encoding="utf-8") as stream:
                value = stream.read(262144)
        except (OSError, UnicodeError):
            continue
        if path == "/proc/cpuinfo":
            match = re.search(r"^Serial\\s*:\\s*([0-9A-Fa-f]{16})\\s*$", value, re.MULTILINE)
            value = match.group(1) if match else ""
        else:
            value = value.strip("\\x00 \\t\\r\\n")
        if re.fullmatch(r"[0-9A-Fa-f]{16}", value) and int(value, 16):
            _BMI30_HARDWARE_SERIAL = value.upper()
            return _BMI30_HARDWARE_SERIAL
    return ""


def _bmi30_hardware_hostname() -> str:
    serial = _bmi30_hardware_serial()
    return "BMI30-" + serial[-9:] if serial else ""


'''


def replace_once(source: str, old: str, new: str) -> str:
    if source.count(old) != 1:
        raise ValueError('Unexpected portal structure: ' + old[:100])
    return source.replace(old, new, 1)


def replace_python_function(source: str, name: str, replacement: str) -> str:
    node = next(n for n in ast.parse(source).body
                if isinstance(n, ast.FunctionDef) and n.name == name)
    lines = source.splitlines(keepends=True)
    return ''.join(lines[:node.lineno - 1]) + replacement.rstrip() + '\n' + ''.join(lines[node.end_lineno:])


def patch(source: str) -> str:
    if PATCH_MARKER in source:
        raise ValueError('Raspberry Pi identity patch is already applied')
    source = replace_once(source, 'def detect_rpi_identity() -> dict[str, str]:',
                          HARDWARE + 'def detect_rpi_identity() -> dict[str, str]:')
    source = replace_once(source, '    info["software_version"] = detect_rpi_software_version()',
                          '    info["serial"] = _bmi30_hardware_serial()\n'
                          '    info["software_version"] = detect_rpi_software_version()')
    source = replace_python_function(source, '_group_short_host_id', '''def _group_short_host_id(hostname: str | None = None) -> str:
    return group_local_rpi_id()
''')
    source = replace_python_function(source, 'group_local_rpi_id', '''def group_local_rpi_id(hostname: str | None = None) -> str:
    # Keep the legacy argument for callers; hostnames are not hardware IDs.
    serial = _bmi30_hardware_serial()
    return serial[-9:] if serial else ""
''')
    source = replace_once(source, '        "hostname": socket.gethostname(),',
                          '        "hostname": _bmi30_hardware_hostname(),\n'
                          '        "rpi_identity_version": RPI_IDENTITY_VERSION,')

    start = source.index('    function _groupDeviceHeaderLabel(')
    end = source.index('    function ', start + len('    function '))
    source = source[:start] + '''    function _groupDeviceHeaderLabel(source, fallbackNode) {{
      return _groupRpiHeaderLabel(source);
    }}
''' + source[end:]

    # A LAN row cannot acquire another board's ID by sharing its IP or bus ID.
    start = source.index('        if (!existing && lan.ip) {{')
    end = source.index('        if (existing) {{', start)
    source = source[:start] + '''        if (!existing && lanUid96) {{
          var uidMatches = devices.filter(function (candidate) {{
            return !candidate.rpi_id &&
              _groupNormalizeUid96(candidate.stm32_uid96) === lanUid96;
          }});
          if (uidMatches.length === 1) {{ existing = uidMatches[0]; }}
        }}
''' + source[end:]

    # Replace the embedded copy with the maintained, tested Group UI source.
    start = source.index('var _groupNetwork = {{}};')
    end = source.index('    function _groupRebuildColumns(', start)
    group_ui = (HERE / 'group_ui.js').read_text(encoding='utf-8')
    group_ui = group_ui.removeprefix('// The RS485 builder remains the fallback. Fast rows join by hardware identity.\n')
    group_literal = group_ui.replace('\\', '\\\\').replace('{', '{{').replace('}', '}}')
    source = source[:start] + group_literal + '\n' + source[end:]
    ast.parse(source)
    return source


def group_javascript(source: str) -> str:
    """Extract the compiled f-string literal, including Python escape handling."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.JoinedStr):
            for value in node.values:
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    text = value.value
                    start = text.find('    function _groupShortHostId(')
                    end = text.find('    function _updateDeviceSensors(', start)
                    if start >= 0 and end > start:
                        return text[start:end]
    raise ValueError('Group JavaScript is not a contiguous portal template literal')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    parser.add_argument('--javascript', type=Path, help='Extract the compiled Group script for validation')
    args = parser.parse_args()
    if args.source.resolve() == args.destination.resolve():
        parser.error('Use a separate destination so the original stays reviewable')
    patched = patch(args.source.read_text(encoding='utf-8'))
    args.destination.parent.mkdir(parents=True, exist_ok=True)
    args.destination.write_text(patched, encoding='utf-8', newline='\n')
    if args.javascript:
        args.javascript.parent.mkdir(parents=True, exist_ok=True)
        args.javascript.write_text(group_javascript(patched), encoding='utf-8', newline='\n')


if __name__ == '__main__':
    main()
