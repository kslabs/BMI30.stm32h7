import importlib.util
import json
from pathlib import Path
import ssl
import tempfile
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "src" / "bmi30_agent.py"
AGENT_UNIT_PATH = Path(__file__).resolve().parents[1] / "systemd" / "bmi30-agent.service"
SPEC = importlib.util.spec_from_file_location("bmi30_agent_under_test", MODULE_PATH)
agent = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(agent)


VALID_DEVICE_ID = "BMI30-0123456789ABCDEF"
VALID_KEY_DATA = "AAAAC3NzaC1lZDI1NTE5AAAAIG9YfqvTAkOgkC6UauorTj84m5B7uQy4F1vKQ2D4Pq8x"
VALID_PUBLIC_KEY = f"ssh-ed25519 {VALID_KEY_DATA} {VALID_DEVICE_ID}@bmi30-tunnel"
VALID_HOST_KEY = f"ssh-ed25519 {VALID_KEY_DATA}"


class FakeResponse:
    def __init__(self, status=200, payload=None):
        self.status = status
        self.payload = payload if payload is not None else {"state": "pending"}

    def read(self, _limit):
        return json.dumps(self.payload).encode("utf-8")


class FakeHTTPSConnection:
    instances = []
    response = FakeResponse()

    def __init__(self, host, port, timeout, context):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.context = context
        self.request_data = None
        self.closed = False
        self.__class__.instances.append(self)

    def request(self, method, path, body, headers):
        self.request_data = (method, path, body, headers)

    def getresponse(self):
        return self.__class__.response

    def close(self):
        self.closed = True


class IdentityTests(unittest.TestCase):
    def test_device_id_always_comes_from_raspberry_serial(self):
        with mock.patch.object(agent, "raspberry_serial", return_value="0123456789ABCDEF"):
            self.assertEqual(
                agent.validate_hardware_identity({"device_id": "BMI30-FEDCBA9876543210"}),
                VALID_DEVICE_ID,
            )
        with (
            mock.patch.object(agent, "raspberry_serial", return_value=None),
            self.assertRaisesRegex(agent.AgentError, "CPU serial"),
        ):
            agent.validate_hardware_identity({"device_id": VALID_DEVICE_ID})

    def test_public_key_comment_must_match_device_id(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "key.pub"
            path.write_text(VALID_PUBLIC_KEY + "\n", encoding="utf-8")
            self.assertEqual(agent.read_public_key(path, VALID_DEVICE_ID), VALID_PUBLIC_KEY)
            with self.assertRaisesRegex(agent.AgentError, "comment"):
                agent.read_public_key(path, "BMI30-FEDCBA9876543210")

    def test_cloned_flash_rotates_credentials_for_real_hardware(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private_key = root / "id_ed25519"
            public_key = root / "id_ed25519.pub"
            token = root / "device_api_token"
            known_hosts = root / "known_hosts"
            tunnel_env = root / "tunnel.env"
            state = root / "state.json"
            bound_serial = root / "bound_raspberry_serial"
            identity_lock = root / "identity.lock"
            config_path = root / "config.json"
            private_key.write_text("copied-private-key\n", encoding="utf-8")
            public_key.write_text(
                VALID_PUBLIC_KEY.replace(VALID_DEVICE_ID, "BMI30-FEDCBA9876543210") + "\n",
                encoding="utf-8",
            )
            token.write_text("t" * 48 + "\n", encoding="ascii")
            known_hosts.write_text("pinned-host-key\n", encoding="utf-8")
            tunnel_env.write_text("REMOTE_PORT=26395\n", encoding="utf-8")
            state.write_text('{"remote_port":26395}\n', encoding="utf-8")
            bound_serial.write_text("FEDCBA9876543210\n", encoding="ascii")
            config = {
                "server_url": agent.PRODUCTION_SERVER_URL,
                "device_id": "BMI30-FEDCBA9876543210",
                "ssh_private_key_path": str(private_key),
                "ssh_public_key_path": str(public_key),
                "ssh_known_hosts_path": str(known_hosts),
                "device_api_token_path": str(token),
                "last_remote_port": 26395,
            }
            agent.save_json(config_path, config)

            with (
                mock.patch.object(agent, "raspberry_serial", return_value="0123456789ABCDEF"),
                mock.patch.object(agent, "stop_new_tunnel") as stop_tunnel,
                mock.patch.object(agent, "_backup_identity_files", return_value=root / "backup"),
                mock.patch.object(agent, "DEFAULT_TUNNEL_ENV", tunnel_env),
                mock.patch.object(agent, "DEFAULT_STATE", state),
                mock.patch.object(agent, "DEFAULT_BOUND_SERIAL", bound_serial),
                mock.patch.object(agent, "DEFAULT_IDENTITY_LOCK", identity_lock),
            ):
                updated, device_id, rotated = agent.reconcile_hardware_identity(config_path, config)

            self.assertTrue(rotated)
            self.assertEqual(device_id, VALID_DEVICE_ID)
            self.assertNotIn("device_id", updated)
            self.assertNotIn("last_remote_port", updated)
            self.assertEqual(agent._saved_public_key_device_id(public_key), VALID_DEVICE_ID)
            self.assertNotEqual(token.read_text(encoding="ascii").strip(), "t" * 48)
            self.assertFalse(tunnel_env.exists())
            self.assertNotIn("remote_port", json.loads(state.read_text(encoding="utf-8")))
            self.assertEqual(bound_serial.read_text(encoding="ascii").strip(), "0123456789ABCDEF")
            stop_tunnel.assert_called_once_with()

    def test_matching_key_without_hardware_binding_is_reinitialized(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private_key = root / "id_ed25519"
            public_key = root / "id_ed25519.pub"
            token = root / "device_api_token"
            state = root / "state.json"
            config_path = root / "config.json"
            private_key.write_text("old-private-key\n", encoding="utf-8")
            public_key.write_text(VALID_PUBLIC_KEY + "\n", encoding="utf-8")
            token.write_text("t" * 48 + "\n", encoding="ascii")
            agent.save_json(
                config_path,
                {
                    "server_url": agent.PRODUCTION_SERVER_URL,
                    "ssh_private_key_path": str(private_key),
                    "ssh_public_key_path": str(public_key),
                    "device_api_token_path": str(token),
                },
            )

            with (
                mock.patch.object(agent, "raspberry_serial", return_value="0123456789ABCDEF"),
                mock.patch.object(agent, "stop_new_tunnel"),
                mock.patch.object(agent, "_backup_identity_files", return_value=root / "backup"),
                mock.patch.object(agent, "_generate_ssh_identity") as generate,
                mock.patch.object(agent, "DEFAULT_TUNNEL_ENV", root / "tunnel.env"),
                mock.patch.object(agent, "DEFAULT_STATE", state),
                mock.patch.object(agent, "DEFAULT_BOUND_SERIAL", root / "bound_raspberry_serial"),
                mock.patch.object(agent, "DEFAULT_IDENTITY_LOCK", root / "identity.lock"),
            ):
                _config, _device_id, rotated = agent.reconcile_hardware_identity(
                    config_path, agent.load_json(config_path)
                )

            self.assertTrue(rotated)
            generate.assert_called_once()


class VersionMetadataTests(unittest.TestCase):
    def test_checkin_uses_raspberry_build_and_real_stm32_version_separately(self):
        local_api = {
            "ok": True,
            "status": {
                "raspberry_firmware_version": "2026-08-12-1450",
                "stm32_firmware_version": "1.2.37",
            },
        }
        with (
            mock.patch.object(agent, "validate_hardware_identity", return_value=VALID_DEVICE_ID),
            mock.patch.object(agent, "read_public_key", return_value=VALID_PUBLIC_KEY),
            mock.patch.object(agent, "local_api_probe", return_value=local_api),
            mock.patch.object(agent, "raspberry_serial", return_value="0123456789ABCDEF"),
            mock.patch.object(agent, "read_first_existing", return_value="Raspberry Pi 5"),
            mock.patch.object(agent, "get_local_ips", return_value=["192.0.2.10"]) as local_ips,
            mock.patch.object(agent, "systemd_state", return_value={"active": "active", "enabled": "enabled"}),
        ):
            metadata = agent.collect_metadata({"ssh_public_key_path": "/unused", "local_port": 80})

        self.assertEqual(metadata["agent_version"], "2026-08-12-1450")
        self.assertEqual(metadata["firmware_version"], "1.2.37")
        self.assertEqual(metadata["connector_version"], "0.2.7")
        self.assertEqual(metadata["hostname"], VALID_DEVICE_ID)
        self.assertEqual(metadata["raspberry_serial"], "0123456789ABCDEF")
        local_ips.assert_called_once_with(local_api)

    def test_local_ips_prefer_live_interfaces_and_exclude_loopback(self):
        local_api = {
            "status": {
                "interfaces": [
                    {"iface": "lo", "ip": "127.0.0.1", "role": "loopback"},
                    {"iface": "wlan0", "ip": "192.168.0.153", "role": "wifi"},
                    {"iface": "old0", "ip": "192.0.2.99", "active": False},
                ]
            }
        }
        with mock.patch.object(agent, "run_command") as fallback:
            self.assertEqual(agent.get_local_ips(local_api), ["192.168.0.153"])
        fallback.assert_not_called()

    def test_stm32_version_is_empty_when_local_api_does_not_report_it(self):
        with mock.patch.object(agent, "raspberry_firmware_version", return_value="2026-08-12-1450"):
            raspberry, stm32 = agent.checkin_versions(
                {
                    "status": {
                        "firmware_release": {"version": "2026-08-12-1450"},
                        "group_state": {"stm32_uid96": "333635343032511300090040"},
                    }
                }
            )
        self.assertEqual(raspberry, "2026-08-12-1450")
        self.assertEqual(stm32, "")


class HTTPSContractTests(unittest.TestCase):
    def setUp(self):
        FakeHTTPSConnection.instances.clear()
        FakeHTTPSConnection.response = FakeResponse()

    def test_system_ca_and_production_path_are_used(self):
        payload = {"device_id": VALID_DEVICE_ID, "public_key": VALID_PUBLIC_KEY}
        token = "t" * 48
        with mock.patch.object(agent.http.client, "HTTPSConnection", FakeHTTPSConnection):
            status, response = agent.system_ca_https_json(
                agent.PRODUCTION_SERVER_URL,
                agent.CHECKIN_PATH,
                payload,
                token=token,
                timeout=7,
            )
        self.assertEqual(status, 200)
        self.assertEqual(response["state"], "pending")
        connection = FakeHTTPSConnection.instances[-1]
        self.assertEqual((connection.host, connection.port), ("www.teiots.net", 443))
        self.assertEqual(connection.context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(connection.context.check_hostname)
        method, path, body, headers = connection.request_data
        self.assertEqual((method, path), ("POST", "/bmi30/api/v1/agent/checkin"))
        self.assertEqual(headers["Authorization"], f"Bearer {token}")
        self.assertNotIn(token.encode("ascii"), body)

    def test_only_exact_production_url_is_allowed(self):
        for value in (
            "http://www.teiots.net/bmi30",
            "https://www.teiots.net/",
            "https://65.21.225.43/bmi30",
            "https://www.teiots.net:444/bmi30",
        ):
            with self.subTest(value=value), self.assertRaises(agent.AgentError):
                agent.validate_server_url(value)

    def test_payload_larger_than_64_kib_is_rejected(self):
        with self.assertRaisesRegex(agent.AgentError, "exceeds"):
            agent.system_ca_https_json(
                agent.PRODUCTION_SERVER_URL,
                agent.CHECKIN_PATH,
                {"large": "x" * agent.MAX_CHECKIN_BYTES},
                token="t" * 48,
                timeout=1,
            )


class ApprovedResponseTests(unittest.TestCase):
    def approved(self):
        return {
            "state": "approved",
            "remote_port": 23456,
            "listen_address": "0.0.0.0",
            "ssh_host": "www.teiots.net",
            "ssh_port": 2222,
            "ssh_user": "bmi30-tunnel",
            "ssh_host_public_key": VALID_HOST_KEY,
        }

    def test_exact_production_assignment_is_accepted(self):
        value = agent.validate_approved_response(self.approved())
        self.assertEqual(value["remote_port"], 23456)

    def test_wrong_host_port_user_and_listen_address_are_rejected(self):
        mutations = {
            "ssh_host": "example.invalid",
            "ssh_port": 22,
            "ssh_user": "root",
            "listen_address": "127.0.0.1",
            "remote_port": 19999,
        }
        for field, value in mutations.items():
            response = self.approved()
            response[field] = value
            with self.subTest(field=field), self.assertRaises(agent.AgentError):
                agent.validate_approved_response(response)

    def test_pinned_host_key_cannot_rotate_automatically(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "known_hosts"
            agent.pin_ssh_host_key(path, "www.teiots.net", 2222, VALID_HOST_KEY)
            first = path.read_text(encoding="utf-8")
            agent.pin_ssh_host_key(path, "www.teiots.net", 2222, VALID_HOST_KEY)
            self.assertEqual(path.read_text(encoding="utf-8"), first)
            replacement = "y" if VALID_KEY_DATA[-1] != "y" else "x"
            different = f"ssh-ed25519 {VALID_KEY_DATA[:-1]}{replacement}"
            with self.assertRaisesRegex(agent.AgentError, "refusing"):
                agent.pin_ssh_host_key(path, "www.teiots.net", 2222, different)


class StateHandlingTests(unittest.TestCase):
    def test_auth_errors_retry_in_about_one_minute(self):
        self.assertEqual(agent.auth_error_retry_interval({}), 60)
        self.assertEqual(agent.auth_error_retry_interval({"auth_error_retry_seconds": 60}), 60)
        self.assertEqual(agent.auth_error_retry_interval({"auth_error_retry_seconds": 10}), 60)
        self.assertEqual(agent.auth_error_retry_interval({"auth_error_retry_seconds": "invalid"}), 60)

    def test_pending_stops_production_tunnel(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            public_key = root / "id.pub"
            public_key.write_text(VALID_PUBLIC_KEY + "\n", encoding="utf-8")
            private_key = root / "id"
            private_key.write_text("private-key-placeholder\n", encoding="utf-8")
            token = root / "token"
            token.write_text("t" * 48 + "\n", encoding="ascii")
            bound_serial = root / "bound_raspberry_serial"
            bound_serial.write_text("0123456789ABCDEF\n", encoding="ascii")
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "server_url": agent.PRODUCTION_SERVER_URL,
                        "ssh_private_key_path": str(private_key),
                        "ssh_public_key_path": str(public_key),
                        "device_api_token_path": str(token),
                        "local_port": 80,
                    }
                ),
                encoding="utf-8",
            )
            calls = []

            def fake_systemctl(*args, **_kwargs):
                calls.append(args)
                return agent.subprocess.CompletedProcess(args, 0, "inactive\n", "")

            with (
                mock.patch.object(agent, "raspberry_serial", return_value="0123456789ABCDEF"),
                mock.patch.object(agent, "collect_metadata", return_value={"device_id": VALID_DEVICE_ID}),
                mock.patch.object(
                    agent,
                    "system_ca_https_json",
                    return_value=(200, {"state": "pending", "next_checkin_seconds": 60}),
                ),
                mock.patch.object(agent, "systemctl", side_effect=fake_systemctl),
                mock.patch.object(agent, "DEFAULT_STATE", root / "state.json"),
                mock.patch.object(agent, "DEFAULT_BOUND_SERIAL", bound_serial),
                mock.patch.object(agent, "DEFAULT_IDENTITY_LOCK", root / "identity.lock"),
            ):
                response = agent.perform_checkin(config_path)
            self.assertEqual(response["state"], "pending")
            self.assertIn(("stop", agent.TUNNEL_SERVICE), calls)

    def test_documented_hardware_mismatch_rotates_and_retries_once(self):
        config = {
            "server_url": agent.PRODUCTION_SERVER_URL,
            "ssh_private_key_path": "/unused-private",
            "ssh_public_key_path": "/unused-public",
            "device_api_token_path": "/unused-token",
        }
        mismatch = {
            "code": "hardware_identity_mismatch",
            "expected_device_id": VALID_DEVICE_ID,
            "reset_identity_required": True,
        }
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state.json"
            config_path = Path(directory) / "config.json"
            agent.save_json(config_path, config)
            with (
                mock.patch.object(
                    agent,
                    "reconcile_hardware_identity",
                    side_effect=[(config, VALID_DEVICE_ID, False), (config, VALID_DEVICE_ID, True)],
                ) as reconcile,
                mock.patch.object(agent, "get_or_create_api_token", return_value="t" * 48),
                mock.patch.object(agent, "collect_metadata", return_value={"device_id": VALID_DEVICE_ID}),
                mock.patch.object(
                    agent,
                    "system_ca_https_json",
                    side_effect=[(409, mismatch), (200, {"state": "pending"})],
                ) as checkin,
                mock.patch.object(agent, "stop_new_tunnel"),
                mock.patch.object(agent, "systemd_state", return_value={}),
                mock.patch.object(agent, "DEFAULT_STATE", state),
            ):
                response = agent.perform_checkin(config_path)

        self.assertEqual(response["state"], "pending")
        self.assertEqual(checkin.call_count, 2)
        self.assertEqual(reconcile.call_count, 2)
        self.assertTrue(reconcile.call_args_list[1].kwargs["force_reset"])

    def test_other_409_preserves_identity_and_is_not_retried(self):
        config = {
            "server_url": agent.PRODUCTION_SERVER_URL,
            "ssh_private_key_path": "/unused-private",
            "ssh_public_key_path": "/unused-public",
            "device_api_token_path": "/unused-token",
        }
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state.json"
            config_path = Path(directory) / "config.json"
            agent.save_json(config_path, config)
            with (
                mock.patch.object(
                    agent,
                    "reconcile_hardware_identity",
                    return_value=(config, VALID_DEVICE_ID, False),
                ) as reconcile,
                mock.patch.object(agent, "get_or_create_api_token", return_value="t" * 48),
                mock.patch.object(agent, "collect_metadata", return_value={"device_id": VALID_DEVICE_ID}),
                mock.patch.object(
                    agent,
                    "system_ca_https_json",
                    return_value=(409, {"code": "key_conflict"}),
                ) as checkin,
                mock.patch.object(agent, "systemd_state", return_value={}),
                mock.patch.object(agent, "DEFAULT_STATE", state),
                self.assertRaises(agent.CheckinHTTPError),
            ):
                agent.perform_checkin(config_path)

        self.assertEqual(checkin.call_count, 1)
        self.assertEqual(reconcile.call_count, 1)


class SystemdSandboxTests(unittest.TestCase):
    def test_agent_can_write_automatic_reenrollment_backups(self):
        unit_text = AGENT_UNIT_PATH.read_text(encoding="utf-8")
        read_write_line = next(
            line for line in unit_text.splitlines() if line.startswith("ReadWritePaths=")
        )
        self.assertIn("/var/backups/bmi30-agent", read_write_line.split("=")[1].split())


if __name__ == "__main__":
    unittest.main()
