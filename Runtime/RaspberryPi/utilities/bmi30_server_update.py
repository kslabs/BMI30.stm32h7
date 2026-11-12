#!/usr/bin/env python3
"""Explicit server update/publish and local rollback for complete BMI30 bundles."""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import http.client
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid

from bmi30_server_files import (
    Client, CONFIG, FirmwareError, HTTPError, MAX_ARCHIVE, atomic_write, env_bytes,
    extract_verified, file_sha, marker, read_env, read_json, settings, utcnow,
    verify_bundle, verify_file, version, write_json,
)

import bmi30_portable_assets as portable_assets

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_TOOLS = ("utilities/bmi30_server_files.py", "utilities/bmi30_server_update.py",
                 "utilities/bmi30_portable_assets.py", "utilities/bmi30_runtime_policy.py",
                 "utilities/server_firmware.sh", "utilities/menu.sh", "utilities/list_tools.sh",
                 "utilities/portal_firmware_operation.sh", "utilities/create_bmi30_split_bundle.sh",
                 "utilities/migrate_system_between_disks.sh", "utilities/retire_drive_firmware.sh",
                 "switch_bmi30_split_versions.sh")


SAFE_CENTRAL_SERVICE_HASHES = ('4322a4ddb28815f6470e140b47e460a61b1fe8888ae9ddaf758f2bb348e34945', 'b4abc246559863f1144db841007c63d692d93de56b8eb2db6a81484c2e38fbbe', '6b9a0ef7ea855cd5cbee4764a823a1830cf31b1bfaeb1d1548afa520b931edc8')

def assert_legacy_network_restore_safe(snapshot):
    # No radio/credential mutation. A legacy service must not regain scoped
    # downloaded secrets retained by NetworkManager after the daemon stopped.
    # The marker may have been removed while RAM profiles still survive.
    # Presence/absence is never proof that a legacy service is safe.
    source = Path(snapshot) / 'portable_system/files/opt/bmi30-network/service.py'
    saved = Path(snapshot) / 'portable_system/files'
    marker = saved / 'etc/bmi30-network/central-wifi-ram-only'
    helper = saved / 'opt/bmi30-network/central_wifi_runtime.py'
    network = saved / 'opt/bmi30-network/network.py'
    if (source.is_file() and hashlib.sha256(source.read_bytes()).hexdigest() in SAFE_CENTRAL_SERVICE_HASHES
        and marker.is_file() and not marker.is_symlink() and marker.read_bytes() == b'1\n'
        and helper.is_file() and hashlib.sha256(helper.read_bytes()).hexdigest() == '79d19b454185cd879929941f2d0e27421044ff5b058ec594bb7b700edd9b88e9'
        and network.is_file() and hashlib.sha256(network.read_bytes()).hexdigest() in ('29c7cba7ccd22c15b885983d0d5b91e9cc724da1fe48d18a5afd523d34400bd3', 'a6611b5a155107a940e3300af8643bbe61c70f9698641822d33489981e03d449')):
        return
    program = "import sys,json;sys.path.insert(0,'/opt/bmi30-central-wifi');from rollback_guard import assert_legacy_safe;print(json.dumps(assert_legacy_safe()))"
    outcome = subprocess.run(['/usr/bin/python3', '-I', '-B', '-c', program],
                             capture_output=True, text=True, timeout=120)
    try:
        result = json.loads(outcome.stdout) if outcome.returncode == 0 else {}
    except Exception:
        result = {}
    if result.get('ok') is not True or result.get('central_RAM_profile_count') != 0:
        raise FirmwareError('Central RAM networks remain; legacy restore is blocked until explicit manual cleanup. No connection was deleted or disconnected.')


class Updater:
    def __init__(self, root=ROOT, client=None):
        self.offline = os.getenv("BMI30_OFFLINE_ACTIVATION") == "1"
        if self.offline and (os.geteuid() != 0 or Path('/proc/1/root').exists()
                             or Path('/run/systemd/system').exists() or Path('/sys/class/pwm').exists()):
            raise FirmwareError("Offline activation requires an isolated root chroot without host proc/sys/run")
        self.root = Path(root).resolve()
        self.state = self.root / ".bmi30_cloud_sync"
        self.private = self.state / "server"
        self.incoming = self.private / "incoming"
        self.bundles = self.root / "host/bmi30_split_bundles"
        self.client = client

    def remote(self):
        if self.client is None:
            self.client = Client()
        return self.client

    @contextlib.contextmanager
    def lock(self):
        self.private.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.private.chmod(0o700)
        with (self.state / "server_operation.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise FirmwareError("Another server firmware operation is running") from exc
            yield

    def current(self):
        active = read_env(self.root / "host/bmi30_split_active_version.env")
        release = read_env(self.root / "host/bmi30_firmware_release.env")
        if active.get("BMI30_SPLIT_BUNDLE_ID") != release.get("BMI30_FIRMWARE_BUNDLE_ID"):
            raise FirmwareError("Active bundle and firmware manifest disagree")
        return {"version": release.get("BMI30_FIRMWARE_VERSION", ""),
                "bundle_id": active.get("BMI30_SPLIT_BUNDLE_ID", ""),
                "label": release.get("BMI30_FIRMWARE_LABEL", ""),
                "created_at": release.get("BMI30_FIRMWARE_CREATED_AT", "")}

    def check(self):
        current = self.current()
        try:
            info, _ = self.remote().latest()
        except HTTPError as exc:
            if exc.status == 404 and exc.code == "no_production_release":
                return {"ok": True, "source": "server", "current": current, "available": False,
                        "cloud": {}, "message": "No firmware release has been published on the server."}
            raise
        local_version = version(current["version"])
        remote_version = version(info["FIRMWARE_VERSION"])
        if local_version == remote_version:
            if current["bundle_id"] != info["FIRMWARE_BUNDLE_ID"]:
                raise FirmwareError("Release conflict: equal version has another bundle")
            installed = read_json(self.private / "installed.json", {})
            if installed.get("FIRMWARE_VERSION") == current["version"] and installed != info:
                raise FirmwareError("Release conflict: equal version has another archive/SHA")
        cloud = {"version": info["FIRMWARE_VERSION"], "bundle_id": info["FIRMWARE_BUNDLE_ID"],
                 "archive_name": info["ARCHIVE_NAME"], "archive_sha256": info["ARCHIVE_SHA256"],
                 "archive_size": int(info["ARCHIVE_SIZE"]), "source": "server"}
        return {"ok": True, "source": "server", "current": current, "cloud": cloud,
                "available": remote_version > local_version, "marker": info}

    def progress(self, action, value, message, status="running", error=""):
        path = Path(os.getenv("BMI30_PROGRESS_FILE", str(self.state / "portal_operation.json")))
        write_json(path, {"action": action, "status": status, "progress": value,
                          "message": message, "error": error, "updated_at": int(time.time())})

    def snapshot(self, directory):
        directory.mkdir(parents=True, exist_ok=False, mode=0o700)
        current = self.current()
        verify_bundle(self.bundles / current["bundle_id"], current["bundle_id"])
        paths = ["host/bmi30_split_active_version.env", "host/bmi30_firmware_release.env",
                 "host/bmi30_config.json", "host/bmi30_sel.json"]
        file_metadata = {}
        def remember_file(src, key):
            info = src.stat()
            file_metadata[key] = {"mode": info.st_mode & 0o777,
                                  "uid": info.st_uid, "gid": info.st_gid}
        for name in paths:
            src = self.root / name
            if src.exists():
                remember_file(src, Path(name).name)
                dst = directory / Path(name).name
                shutil.copy2(src, dst)
                dst.chmod(0o600)
        for name in ("dc_offset_samples.npz", "dc_offset_samples.npz.bak", "USB_config.json", "plot_config.json"):
            src = self.root / "host/bmi30_active_runtime/project/host" / name
            if src.is_file():
                remember_file(src, "device_data/" + name)
                dst = directory / "device_data" / name
                dst.parent.mkdir(exist_ok=True, mode=0o700)
                shutil.copy2(src, dst)
                dst.chmod(0o600)
        # Filter files are part of failed-transaction recovery, not normal
        # version rollback. Keep their private bytes with the config snapshot.
        filters = self.root / "host/bmi30_active_runtime/project/host/tag_response_filters"
        if filters.is_dir():
            if filters.is_symlink():
                raise FirmwareError("Filter state must not be a symlink")
            for src in filters.rglob("*"):
                if src.is_symlink():
                    raise FirmwareError("Filter state must not contain symlinks")
                if not src.is_file():
                    continue
                key = "device_filters/" + src.relative_to(filters).as_posix()
                remember_file(src, key)
                dst = directory / key
                dst.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                shutil.copy2(src, dst)
                dst.chmod(0o600)
        for name, src in (("rollback_state.before", self.state / "rollback_state.env"),
                          ("rollback.before.json", self.private / "rollback.json")):
            if src.exists():
                remember_file(src, name)
                shutil.copy2(src, directory / name)
        portable_assets.snapshot(directory / "portable_system")
        record = {**current, "saved_at": utcnow(), "snapshot": str(directory),
                  "installed": read_json(self.private / "installed.json"),
                  "file_metadata": file_metadata}
        write_json(directory / "state.json", record, 0o600)
        return record

    def remember_original(self):
        path = self.private / "original/state.json"
        if path.exists():
            return read_json(path)
        return self.snapshot(path.parent)

    def backup_local(self):
        directory = self.root / "backups" / ("local_snapshot_" + dt.datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8])
        record = self.snapshot(directory)
        shutil.copytree(self.bundles / record["bundle_id"], directory / "bundle")
        verify_bundle(directory / "bundle", record["bundle_id"])
        return {"ok": True, "snapshot": str(directory), "current": self.current()}

    def activate(self, bundle_id, release_path, selected_by, *, recovery=False):
        # Use the locally installed switcher, not any installer from an archive.
        env = dict(os.environ, BMI30_AGENT_BOOTSTRAPPED="1", BMI30_RUNTIME_ONLY="1",
                   BMI30_ACTIVATION_RECOVERY_STATE="1" if recovery else "0", BMI30_RELEASE_MANIFEST_OVERRIDE=str(release_path),
                   BMI30_SPLIT_SELECTED_BY_OVERRIDE=selected_by)
        proc = subprocess.run([str(self.root / "switch_bmi30_split_versions.sh"), "--activate", bundle_id],
                              env=env, stdout=sys.stderr, stderr=sys.stderr, timeout=240)
        if proc.returncode:
            raise FirmwareError("Bundle activation failed; the previous runtime must remain active")
        self.health(bundle_id)

    def health(self, bundle_id):
        current = self.current()
        if current["bundle_id"] != bundle_id:
            raise FirmwareError("Active bundle differs after activation")
        active = read_env(self.root / "host/bmi30_split_active_version.env")
        portal = self.root / active["BMI30_PORTAL_PATH"]
        if file_sha(portal) != file_sha("/usr/local/bin/bmi30-hotspot-info-server.py"):
            raise FirmwareError("Installed portal differs from active bundle")
        if self.offline:
            # Bundle, staged Python/JSON and installed portal are checked. The
            # target disk is not booted, so service health cannot be asserted.
            return
        for service in ("bmi30-core", "bmi30-hotspot-info", "bmi30-agent", "bmi30-tunnel"):
            result = subprocess.run(["systemctl", "is-active", service], capture_output=True, text=True, timeout=10)
            if result.returncode or result.stdout.strip() != "active":
                raise FirmwareError("Service is not active: " + service)

    def restore_snapshot_file(self, record, key, target, *, private_config=False):
        source = Path(record["snapshot"]) / key
        info = record.get("file_metadata", {}).get(key)
        if info is None:
            # Older snapshots contain bytes only; retain the current owner and
            # mode rather than applying atomic_write's public-file defaults.
            current = target.stat() if target.exists() else target.parent.stat()
            info = {"mode": current.st_mode & 0o777 if target.exists() else 0o600,
                    "uid": current.st_uid, "gid": current.st_gid}
        mode = int(info["mode"])
        if private_config:
            mode &= 0o640
        atomic_write(target, source.read_bytes(), mode,
                     uid=int(info["uid"]), gid=int(info["gid"]))

    def restore(self, record, *, failed=False):
        snapshot = Path(record["snapshot"])
        if not self.offline:
            assert_legacy_network_restore_safe(snapshot)
        if failed:
            for name in ("bmi30_config.json", "bmi30_sel.json"):
                if (snapshot / name).exists():
                    self.restore_snapshot_file(record, name, self.root / "host" / name,
                                               private_config=name == "bmi30_config.json")
            for src in (snapshot / "device_data").glob("*"):
                self.restore_snapshot_file(record, "device_data/" + src.name,
                                           self.root / "host/bmi30_active_runtime/project/host" / src.name)
            for src in (snapshot / "device_filters").rglob("*"):
                if src.is_file():
                    key = "device_filters/" + src.relative_to(snapshot / "device_filters").as_posix()
                    target = self.root / "host/bmi30_active_runtime/project/host/tag_response_filters" / src.relative_to(snapshot / "device_filters")
                    self.restore_snapshot_file(record, key, target)
        portable_assets.restore(snapshot / "portable_system")
        self.activate(record["bundle_id"], snapshot / "bmi30_firmware_release.env", "server-recovery" if failed else "server-rollback", recovery=failed)
        if record.get("installed"):
            write_json(self.private / "installed.json", record["installed"], 0o600)
        else:
            (self.private / "installed.json").unlink(missing_ok=True)
        if failed:
            for name, dst in (("rollback_state.before", self.state / "rollback_state.env"),
                              ("rollback.before.json", self.private / "rollback.json")):
                if (snapshot / name).exists():
                    self.restore_snapshot_file(record, name, dst)
                else:
                    dst.unlink(missing_ok=True)

    def recover(self):
        pending = read_json(self.private / "pending.json")
        if not pending:
            return
        committed = read_json(self.private / "last_transaction.json", {})
        if committed.get("id") != pending["id"]:
            self.restore(pending["before"], failed=True)
        (self.private / "pending.json").unlink()

    def install_bundle(self, project, info):
        bundle_id = info["FIRMWARE_BUNDLE_ID"]
        source = project / "host/bmi30_split_bundles" / bundle_id
        target = self.bundles / bundle_id
        if target.exists():
            verify_bundle(target, bundle_id)
            if (target / "SHA256SUMS").read_bytes() != (source / "SHA256SUMS").read_bytes():
                raise FirmwareError("Local bundle ID already exists with different contents")
            return
        stage = self.bundles / (".server-stage-" + uuid.uuid4().hex)
        try:
            shutil.copytree(source, stage)
            verify_bundle(stage, bundle_id)
            os.replace(stage, target)
        finally:
            if stage.exists():
                shutil.rmtree(stage)

    def write_rollback(self, before, info):
        write_json(self.private / "rollback.json", before, 0o600)
        values = {"ROLLBACK_AVAILABLE": "1", "ROLLBACK_BUNDLE_ID": before["bundle_id"],
                  "ROLLBACK_FIRMWARE_VERSION": before["version"], "ROLLBACK_LABEL": before["label"],
                  "ROLLBACK_SAVED_AT": before["saved_at"], "UPDATED_TO_VERSION": info["FIRMWARE_VERSION"],
                  "UPDATED_TO_BUNDLE_ID": info["FIRMWARE_BUNDLE_ID"], "ROLLBACK_BACKEND": "server"}
        atomic_write(self.state / "rollback_state.env", env_bytes(values))

    def download(self):
        info, raw = self.remote().latest()
        target = self.remote().download(info, self.incoming)
        atomic_write(self.incoming / "bmi30_latest.env", raw)
        return {"ok": True, "installed": False, "archive": str(target), "marker": info}

    def update(self, force=False):
        self.recover()
        self.progress("update", 3, "Checking server firmware…")
        checked = self.check()
        info = checked.get("marker")
        if not info:
            return checked
        if version(info["FIRMWARE_VERSION"]) < version(checked["current"]["version"]):
            raise FirmwareError("Server version is older; use local rollback explicitly")
        if not checked["available"] and not force:
            return {"ok": True, "changed": False, "message": "Already up to date"}
        self.progress("update", 10, "Downloading and verifying firmware…")
        archive = self.remote().download(info, self.incoming)
        with tempfile.TemporaryDirectory(prefix=".server-extract-", dir=self.root / "host") as directory:
            project = extract_verified(archive, info, Path(directory))
            # Do not install an outdated selection if latest changed during transfer.
            latest, _ = self.remote().latest()
            if latest != info:
                raise FirmwareError("Server latest changed during download; check again")
            self.progress("update", 55, "Saving the current version for rollback…")
            self.remember_original()
            tx_id = uuid.uuid4().hex
            before = self.snapshot(self.private / "transactions" / tx_id)
            self.install_bundle(project, info)
            release = project / "host/bmi30_firmware_release.env"
            pending = {"id": tx_id, "before": before, "target": info}
            write_json(self.private / "pending.json", pending, 0o600)
            try:
                self.progress("update", 75, "Activating verified firmware…")
                self.activate(info["FIRMWARE_BUNDLE_ID"], release, "server-update")
                if self.current()["version"] != info["FIRMWARE_VERSION"]:
                    raise FirmwareError("Installed firmware version mismatch")
                # Commit rollback metadata only after activation and health checks.
                if before["bundle_id"] != info["FIRMWARE_BUNDLE_ID"] or before["version"] != info["FIRMWARE_VERSION"]:
                    self.write_rollback(before, info)
                write_json(self.private / "installed.json", info, 0o600)
                write_json(self.private / "last_transaction.json", {**pending, "completed_at": utcnow()}, 0o600)
            except Exception:
                self.restore(before, failed=True)
                (self.private / "pending.json").unlink(missing_ok=True)
                raise
            (self.private / "pending.json").unlink()
        return {"ok": True, "changed": True, "current": self.current(), "source": "server", "marker": info,
                "activation": "offline" if self.offline else "live", "runtime_checked": not self.offline}

    def rollback(self, original=False):
        self.recover()
        record = read_json(self.private / ("original/state.json" if original else "rollback.json"))
        if not record or record.get("consumed"):
            raise FirmwareError("No saved rollback version is available")
        self.progress("rollback", 10, "Validating the saved original version…" if original else "Validating saved firmware…")
        self.restore(record)
        if not original:
            write_json(self.private / "rollback.json", {**record, "consumed": True}, 0o600)
        existing = self.state / "rollback_state.env"
        values = read_env(existing) if existing.exists() else {}
        values.update(ROLLBACK_AVAILABLE="0", ROLLBACK_CONSUMED_AT=utcnow())
        atomic_write(existing, env_bytes(values))
        return {"ok": True, "current": self.current(), "restored_original": original}

    def build(self):
        current = self.current()
        version(current["version"])
        bundle_id = current["bundle_id"]
        source = self.bundles / bundle_id
        manifest = verify_bundle(source, bundle_id)
        self.ensure_distributable(source, manifest)
        return self.build_archive(current, source)

    @staticmethod
    def ensure_distributable(source, manifest):
        # Runtime bundles for distribution must carry defaults, never a device's
        # portal password, network settings, saved mode or calibration.
        for name in (manifest["BMI30_BUNDLE_PROJECT_CONFIG_REL"], "system/etc/bmi30/portal_config.json",
                     "system/usr/local/bin/host/bmi30_config.json", "project/host/bmi30_sel.json"):
            p = source / name
            if p.exists() and json.loads(p.read_text()) != {}:
                raise FirmwareError("Create a distributable bundle with device configuration removed before publishing")
        for file in source.rglob("*"):
            if file.is_file() and ("player_recordings" in file.parts or file.suffix in {".key", ".pem", ".wav", ".npz", ".log", ".jsonl"} or file.name in {"device_api_token", "id_ed25519", "dc_offset_samples.npz", "dc_offset_samples.npz.bak", "USB_config.json", "plot_config.json"}):
                raise FirmwareError("Device data must not be included in a public firmware bundle")

    def build_archive(self, current, source):
        bundle_id = current["bundle_id"]
        output = self.root / "backups/server_releases" / current["version"]
        marker_path = output / "bmi30_latest.env"
        identity = {"bundle_checksums_sha256": file_sha(source / "SHA256SUMS"),
                    "release_manifest_sha256": file_sha(self.root / "host/bmi30_firmware_release.env"),
                    "tool_checksums": {name: file_sha(self.root / name) for name in ARCHIVE_TOOLS
                                       if (self.root / name).is_file()}}
        if marker_path.exists():
            info = marker(marker_path.read_bytes())
            archive = output / info["ARCHIVE_NAME"]
            verify_file(archive, info)
            if info["FIRMWARE_BUNDLE_ID"] != bundle_id:
                raise FirmwareError("Existing release version belongs to another bundle")
            if read_json(output / "build_identity.json") != identity:
                raise FirmwareError("Release contents changed; create a new version before publishing")
            return info, archive, marker_path
        output.mkdir(parents=True, exist_ok=True)
        archive = output / ("bmi30_backup_" + dt.datetime.now().strftime("%Y%m%d_%H%M%S") + ".tar.gz")
        temporary = archive.with_suffix(archive.suffix + ".part")
        with tempfile.TemporaryDirectory(prefix=".server-build-", dir=self.root / "host") as directory:
            project = Path(directory) / "Documents"
            target = project / "host/bmi30_split_bundles" / bundle_id
            shutil.copytree(source, target)
            for name in ("bmi30_split_active_version.env", "bmi30_firmware_release.env"):
                (project / "host" / name).write_bytes(env_bytes(read_env(self.root / "host" / name)))
            # Include portable source/tools for recovery; installation only imports
            # the selected bundle and never executes incoming maintenance scripts.
            for name in ARCHIVE_TOOLS:
                if (self.root / name).is_file():
                    dst = project / name; dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(self.root / name, dst)
            with tarfile.open(temporary, "w:gz") as tar:
                tar.add(project, arcname="Documents")
        os.replace(temporary, archive)
        info = {"FIRMWARE_VERSION": current["version"], "FIRMWARE_BUNDLE_ID": bundle_id,
                "ARCHIVE_NAME": archive.name, "ARCHIVE_SIZE": str(archive.stat().st_size), "ARCHIVE_SHA256": file_sha(archive)}
        info = marker(env_bytes(info))
        with tempfile.TemporaryDirectory(prefix=".server-validate-", dir=self.root / "host") as directory:
            extract_verified(archive, info, Path(directory))
        write_json(output / "build_identity.json", identity)
        atomic_write(marker_path, env_bytes(info))
        return info, archive, marker_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["backend", "configure", "check", "download", "update", "rollback", "restore-original", "remember-original", "backup-local", "build", "publish", "status"])
    parser.add_argument("--project", type=Path, default=ROOT)
    parser.add_argument("--backend", choices=["server"])
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--marker", type=Path)
    parser.add_argument("--archive", type=Path)
    args = parser.parse_args()
    updater = Updater(args.project)
    action = "rollback" if args.command in {"rollback", "restore-original"} else "update"
    try:
        if args.command == "backend":
            print(settings()["backend"]); return 0
        if args.command == "configure":
            if not args.backend:
                raise FirmwareError("configure requires --backend server")
            write_json(CONFIG, {"backend": args.backend})
            result = {"ok": True, "backend": args.backend}
        elif args.command == "check":
            result = updater.check()
        elif args.command == "status":
            result = {"ok": True, "backend": settings()["backend"], "current": updater.current(),
                      "original": read_json(updater.private / "original/state.json"),
                      "rollback": read_json(updater.private / "rollback.json"), "update": updater.check()}
        else:
            with updater.lock():
                if args.command == "remember-original":
                    result = updater.remember_original()
                elif args.command == "backup-local":
                    result = updater.backup_local()
                elif args.command == "download":
                    result = updater.download()
                elif args.command == "update":
                    result = updater.update(force=args.force)
                elif args.command in {"rollback", "restore-original"}:
                    result = updater.rollback(original=args.command == "restore-original")
                else:
                    if args.marker or args.archive:
                        if not args.marker or not args.archive:
                            raise FirmwareError("Both --marker and --archive are required")
                        info, archive, marker_path = marker(args.marker.read_bytes()), args.archive, args.marker
                        with tempfile.TemporaryDirectory(prefix=".server-validate-", dir=args.project / "host") as directory:
                            project = extract_verified(archive, info, Path(directory))
                            bundle = project / "host/bmi30_split_bundles" / info["FIRMWARE_BUNDLE_ID"]
                            updater.ensure_distributable(bundle, read_env(bundle / "manifest.env"))
                    else:
                        info, archive, marker_path = updater.build()
                    result = {"ok": True, "archive": str(archive), "marker_path": str(marker_path), "marker": info}
                    if args.command == "publish":
                        result["publication"] = updater.remote().publish(info, marker_path.read_bytes(), archive)
                        current = updater.current()
                        if current["version"] == info["FIRMWARE_VERSION"] and current["bundle_id"] == info["FIRMWARE_BUNDLE_ID"]:
                            write_json(updater.private / "installed.json", info, 0o600)
                        write_json(archive.parent / "publication.json", result)
        if args.command in {"update", "rollback", "restore-original"}:
            updater.progress(action, 100, result.get("message", "Firmware operation completed."), "succeeded")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (FirmwareError, OSError, ValueError, EOFError, http.client.HTTPException, tarfile.TarError, subprocess.SubprocessError) as exc:
        # Never dump HTTP request objects, headers or token file contents.
        error = str(exc) if isinstance(exc, FirmwareError) else type(exc).__name__ + ": operation failed"
        if args.command in {"update", "rollback", "restore-original"}:
            updater.progress(action, 95, "Firmware operation failed.", "failed", error)
        print(json.dumps({"ok": False, "error": error}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
