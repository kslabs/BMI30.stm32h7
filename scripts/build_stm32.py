#!/usr/bin/env python3
"""Verify the pinned STM32 baseline with an offline build.

Copy the manifest-listed working sources and frozen builder into a unique local
.tmp directory, then rebuild using the repository SDK. The frozen builder checks
every input and requires a byte-identical release BIN. Modified sources fail
verification; this command does not refresh the pinned specification. It never
flashes a board or accesses the network. Build outputs remain in .tmp for review.
"""

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import uuid


ROOT = Path(__file__).resolve().parents[1]
SUPPORT = ROOT / "BuildSupport" / "STM32"
SDK = ROOT / "SDK" / "STM32Cube_FW_H7_V1.12.1"


def input_file(root, relative):
    """Resolve a regular input without allowing paths outside its source root."""
    relative = Path(relative)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Unsafe input path: " + str(relative))
    source = (root / relative).resolve()
    if not source.is_relative_to(root.resolve()) or not source.is_file():
        raise ValueError("Missing or redirected input: " + str(relative))
    return source


def find_toolchain():
    installed = Path(r"C:\ST\STM32CubeCLT_1.19.0\GNU-tools-for-STM32\bin")
    if (installed / "arm-none-eabi-gcc.exe").is_file():
        return installed
    compiler = shutil.which("arm-none-eabi-gcc")
    if compiler:
        return Path(compiler).resolve().parent
    raise ValueError("GNU Tools for STM32 not found; provide --toolchain BIN_DIR")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--toolchain", type=Path,
        help="GNU Tools for STM32 bin directory; defaults to the installed CLT or PATH",
    )
    parser.add_argument("--jobs", type=int, default=4, help="Parallel compiler jobs (1-16)")
    args = parser.parse_args()
    if not 1 <= args.jobs <= 16:
        parser.error("--jobs must be between 1 and 16")
    toolchain = args.toolchain.resolve() if args.toolchain else find_toolchain()

    spec_path = input_file(SUPPORT, "build-spec.json")
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    manifest_path = input_file(SUPPORT, spec["project_tree_manifest"]["path"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or not manifest:
        raise ValueError("Project source manifest must be a nonempty object")
    sources = [(relative, input_file(ROOT, relative)) for relative in manifest]
    builder_files = []
    for source in sorted(SUPPORT.rglob("*")):
        if source.is_symlink():
            raise ValueError("Builder inputs must not be symlinks: " + str(source))
        if source.is_file():
            relative = source.relative_to(SUPPORT)
            builder_files.append((relative, input_file(SUPPORT, relative)))
    input_file(SUPPORT, "build_source.py")
    if not SDK.is_dir() or not SDK.resolve().is_relative_to(ROOT):
        raise ValueError("Repository STM32Cube H7 SDK is missing or redirected")

    temporary = ROOT / ".tmp"
    temporary.mkdir(exist_ok=True)
    if not temporary.resolve().is_relative_to(ROOT):
        raise ValueError("Local build staging directory is outside the repository")
    stage = temporary / ("stm32-baseline-" + uuid.uuid4().hex)
    stage.mkdir(exist_ok=False)
    project = stage / "project"
    builder = stage / "builder"
    output = stage / "build"
    for target_root, entries in ((project, sources), (builder, builder_files)):
        for relative, source in entries:
            destination = target_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)

    print("Pinned baseline verification; modified inputs will fail.", flush=True)
    print("Build staging: " + str(stage), flush=True)
    result = subprocess.run(
        [sys.executable, str(builder / "build_source.py"),
         "--project", str(project), "--output", str(output),
         "--toolchain", str(toolchain), "--cube-h7", str(SDK),
         "--jobs", str(args.jobs)],
        cwd=ROOT,
    )
    report = output / "build-report.json"
    if report.is_file():
        print("Build report: " + str(report), flush=True)
    else:
        print("Build stopped before a report; inspect: " + str(stage), flush=True)
    return result.returncode


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError) as error:
        print("STM32 baseline verification failed: " + str(error), file=sys.stderr)
        sys.exit(1)
