#!/usr/bin/env python3
"""
Generate Core/Inc/build_autogen.h with current local date/time to force
firmware to embed fresh build timestamp on every build.

Usage:
  py -3 .vscode/gen_build_autogen.py ../Core/Inc/build_autogen.h
"""
import sys
from datetime import datetime
from pathlib import Path

TEMPLATE = """/* Auto-generated, do not edit manually. */
#ifndef BUILD_AUTOGEN_H
#define BUILD_AUTOGEN_H
#define BUILD_AUTOGEN_DATE "{date}"
#define BUILD_AUTOGEN_TIME "{time}"
#endif /* BUILD_AUTOGEN_H */
"""

def main():
    if len(sys.argv) < 2:
        print("Usage: gen_build_autogen.py <output_header>")
        return 2
    out_path = Path(sys.argv[1])
    now = datetime.now()
    content = TEMPLATE.format(date=now.strftime('%Y-%m-%d'), time=now.strftime('%H:%M:%S'))

    # Create parent directories if missing
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Write only if changed to avoid needless rebuild noise
    if out_path.exists():
        old = out_path.read_text(encoding='utf-8', errors='ignore')
        if old == content:
            print(f"[AUTOGEN] Up-to-date: {out_path}")
            return 0

    out_path.write_text(content, encoding='utf-8')
    print(f"[AUTOGEN] Wrote: {out_path}")
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
