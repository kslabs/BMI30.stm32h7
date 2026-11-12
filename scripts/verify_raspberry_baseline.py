"""Verify the promoted Raspberry baseline offline, without changing any files."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shlex
import tarfile


VERSION = "2026-11-06-151000"
ARCHIVE_NAME = "bmi30_backup_20261106_151000.tar.gz"
ARCHIVE_SHA256 = "129555be6524ee69f036516d2d03de086a5ab71d6c74cfb9a5e5dfccc03f8ba8"
MEMBER_COUNT = 173
SUM_COUNT = 158
DOCUMENTS = "/home/techaid/Documents/"
ACTIVE_PROJECT = "host/bmi30_active_runtime/project/"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_relative(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    require(bool(name) and not path.is_absolute(), f"Absolute or empty path: {name!r}")
    require(str(path) == name and all(p not in (".", "..") for p in path.parts),
            f"Non-canonical path: {name!r}")
    require("\\" not in name and ":" not in name and "\x00" not in name,
            f"Unsafe path: {name!r}")
    return path


def regular_file(base: Path, name: str) -> Path:
    relative = safe_relative(name)
    require(base.is_dir() and not base.is_symlink(), f"Unsafe baseline directory: {base}")
    path = base
    for part in relative.parts:
        path = path / part
        require(not path.is_symlink(), f"Symlink is not allowed: {path}")
    require(path.is_file(), f"Missing regular file: {path}")
    require(path.resolve().is_relative_to(base.resolve()), f"Path escapes baseline: {name}")
    return path


def parse_env(data: bytes) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in data.decode("utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        key, separator, raw = line.partition("=")
        require(bool(separator) and bool(re.fullmatch(r"[A-Z][A-Z0-9_]*", key)),
                "Malformed baseline env entry")
        require(key not in result, f"Duplicate env key: {key}")
        values = shlex.split(raw, comments=False, posix=True)
        require(len(values) <= 1, f"Malformed env value: {key}")
        result[key] = values[0] if values else ""
    return result


def verify(root: Path) -> dict[str, object]:
    runtime = root / "Runtime" / "RaspberryPi"
    archive = root / "FirmwareReleases" / "RaspberryPi" / VERSION / ARCHIVE_NAME
    require(archive.is_file() and not archive.is_symlink(), "Canonical release archive is missing or a symlink")
    require(sha256(archive.read_bytes()) == ARCHIVE_SHA256, "Canonical release archive SHA256 mismatch")
    for parent in (runtime.parent, runtime):
        require(parent.is_dir() and not parent.is_symlink(), f"Unsafe runtime directory: {parent}")

    expected: dict[str, bytes] = {}
    with tarfile.open(archive, "r:gz") as source:
        for member in source:
            require(member.isfile() and not member.issym() and not member.islnk(),
                    f"Archive contains a non-regular member: {member.name}")
            path = safe_relative(member.name)
            require(path.parts[0] == "Documents" and len(path.parts) > 1,
                    f"Archive member is outside Documents: {member.name}")
            name = str(PurePosixPath(*path.parts[1:]))
            require(name not in expected, f"Duplicate archive member: {name}")
            stream = source.extractfile(member)
            require(stream is not None, f"Cannot read archive member: {name}")
            expected[name] = stream.read()
    require(len(expected) == MEMBER_COUNT, f"Expected {MEMBER_COUNT} archive members; found {len(expected)}")
    for name, original in expected.items():
        require(regular_file(runtime, name).read_bytes() == original,
                f"Promoted baseline differs from release: {name}")

    # Check the whole promoted tree, including unexpected added directories.
    for path in runtime.rglob("*"):
        require(not path.is_symlink(), f"Symlink is not allowed: {path}")

    bundle_relative = f"host/bmi30_split_bundles/{VERSION}"
    bundle = runtime / bundle_relative
    sums_name = bundle_relative + "/SHA256SUMS"
    sums: dict[str, str] = {}
    for line in expected[sums_name].decode("utf-8").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64}) ([ *])(.+)", line)
        require(match is not None, "Malformed GNU SHA256SUMS entry")
        digest, _, name = match.groups()
        safe_relative(name)
        require(name not in sums and name != "SHA256SUMS", f"Invalid duplicate/self checksum: {name}")
        sums[name] = digest
        require(sha256(regular_file(bundle, name).read_bytes()) == digest,
                f"Bundle SHA256 mismatch: {name}")
    require(len(sums) == SUM_COUNT, f"Expected {SUM_COUNT} bundle checksums; found {len(sums)}")
    prefix = bundle_relative + "/"
    members = {name[len(prefix):] for name in expected if name.startswith(prefix)}
    require(set(sums) == members - {"SHA256SUMS"}, "Bundle checksum coverage is incomplete")

    active = parse_env(expected["host/bmi30_split_active_version.env"])
    manifest = parse_env(expected[bundle_relative + "/manifest.env"])
    require(active.get("BMI30_SPLIT_BUNDLE_ID") == VERSION, "Outer active bundle differs from baseline")
    require(manifest.get("BMI30_BUNDLE_ID") == VERSION, "Bundle manifest ID differs from baseline")
    references = {
        "core": ("BMI30_CORE_PATH", "BMI30_BUNDLE_CORE_REL"),
        "engine": ("BMI30_ENGINE_SOURCE", "BMI30_BUNDLE_ENGINE_REL"),
        "gui": ("BMI30_GUI_PATH", "BMI30_BUNDLE_GUI_REL"),
    }
    resolved: dict[str, str] = {}
    for component, (active_key, manifest_key) in references.items():
        target = active.get(active_key, "")
        if target.startswith(DOCUMENTS):
            target = target[len(DOCUMENTS):]
        require(target.startswith(ACTIVE_PROJECT), f"Invalid active {component} reference")
        relative = "project/" + target[len(ACTIVE_PROJECT):]
        require(manifest.get(manifest_key) == relative, f"Active/manifest {component} reference mismatch")
        regular_file(bundle, relative)
        require(relative in sums, f"Active {component} is not covered by bundle checksums")
        resolved[component] = relative

    # This inherited nested pointer is part of the exact published release.
    # The outer selection and bundle manifest above remain authoritative.
    nested = parse_env(expected[bundle_relative + "/project/host/bmi30_split_active_version.env"])
    return {
        "ok": True,
        "version": VERSION,
        "archive_sha256": ARCHIVE_SHA256,
        "regular_files_verified": len(expected),
        "bundle_checksums_verified": len(sums),
        "active_components": resolved,
        "preserved_nested_bundle_id": nested.get("BMI30_SPLIT_BUNDLE_ID"),
        "files_changed": False,
        "device_or_network_access": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1],
                        help="Repository root (defaults to this script's project root)")
    args = parser.parse_args()
    try:
        report = verify(args.root.absolute())
    except (OSError, ValueError, KeyError, tarfile.TarError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
