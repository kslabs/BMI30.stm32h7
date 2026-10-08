"""Write a GCC response file without depending on a Unix shell."""

import sys
from pathlib import Path


def main():
    if len(sys.argv) < 3:
        raise SystemExit("Usage: gen_objects_list.py <output> <object> [...]")
    objects = (f'"{name}"' for name in sys.argv[2:])
    Path(sys.argv[1]).write_text("\n".join(objects) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
