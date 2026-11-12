import ipaddress
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import bmi30_remote_ssh as client

TEST_TEMP = Path(__file__).resolve().parents[1] / '.tmp' / 'remote-ssh-tests'
TEST_TEMP.mkdir(parents=True, exist_ok=True)


class DiscoverySafetyTests(unittest.TestCase):
    def test_only_bounded_local_networks(self):
        with patch.object(client, 'powershell_json', return_value=[
            {'IPAddress': '192.168.0.199', 'PrefixLength': 24},
            {'IPAddress': '10.1.2.3', 'PrefixLength': 8},
            {'IPAddress': '8.8.8.8', 'PrefixLength': 24},
        ]):
            addresses = client.local_candidates()
        self.assertEqual(len(addresses), 253)
        self.assertNotIn('192.168.0.199', addresses)
        self.assertTrue(all(ipaddress.ip_address(x) in ipaddress.ip_network('192.168.0.0/24') for x in addresses))

    def test_duplicate_identity_not_opened(self):
        found = client.unique_devices([
            {'name': 'BMI30-A', 'ip': '192.168.0.2'},
            {'name': 'BMI30-A', 'ip': '192.168.0.3'},
            {'name': 'BMI30-B', 'ip': '192.168.0.4'}, None])
        self.assertEqual(list(found), ['BMI30-B'])

    def test_ip_changes_preserve_alias_and_key_check(self):
        key, known = Path('C:/Users/Test/.ssh/key'), Path('C:/Users/Test/.ssh/known')
        old = client.render_config({'BMI30-A': {'ip': '192.168.0.2'}}, key, known)
        new = client.render_config({'BMI30-A': {'ip': '192.168.0.99'}}, key, known)
        self.assertEqual(old.replace('192.168.0.2', '192.168.0.99'), new)
        self.assertIn('HostKeyAlias bmi30-a', new)
        self.assertIn('StrictHostKeyChecking ask', new)

    def test_untrusted_names_cannot_inject_config(self):
        for name in ['BMI30-A\nProxyCommand bad', 'other-host', 'BMI30;exit', 'BMI30-A space']:
            with self.assertRaises(ValueError):
                client.render_config({name: {'ip': '192.168.0.2'}}, Path('key'), Path('known'))

    def test_ssh_hostname_must_match_discovery(self):
        response = type('Result', (), {'returncode': 0, 'stdout': 'BMI30-OTHER\n', 'stderr': ''})()
        with patch.object(client.subprocess, 'run', return_value=response):
            self.assertFalse(client.authenticated('BMI30-A')[0])

    def test_unknown_host_key_not_accepted_unattended(self):
        args = client.ssh_args('BMI30-A')
        self.assertIn('BatchMode=yes', args)
        self.assertIn('StrictHostKeyChecking=yes', args)

    def test_remote_folders_open_in_separate_windows(self):
        response = type('Result', (), {'returncode': 0, 'stderr': ''})()
        with patch.object(client, 'code_paths', return_value=(Path('Code.exe'), Path('cli.js'))), patch.object(
            client.subprocess, 'run', return_value=response) as run:
            client.open_board('BMI30-A', {'code_cmd': 'code.cmd', 'remote_folder': '/home/techaid/Documents'})
        args = run.call_args.args[0]
        self.assertIn('--new-window', args)
        self.assertEqual(args[-1], 'vscode-remote://ssh-remote+BMI30-A/home/techaid/Documents')

    def test_existing_remote_window_not_duplicated_on_restart(self):
        report = "window [2] (Documents [SSH: BMI30-A])\nConnection to 'ssh-remote+BMI30-B' could not be established\n"
        response = type('Result', (), {'stdout': report})()
        with patch.object(client, 'code_paths', return_value=(Path('Code.exe'), Path('cli.js'))), patch.object(
            client.subprocess, 'run', return_value=response):
            self.assertEqual(client.opened_windows({'code_cmd': 'code.cmd'}), {'BMI30-A', 'BMI30-B'})

    def test_key_enrollment_only_sends_public_key(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as directory:
            root = Path(directory)
            (root / 'id_ed25519_bmi30.pub').write_text('ssh-ed25519 AAAA test-pc')
            with patch.object(client, 'SSH_ROOT', root), patch.object(client.subprocess, 'call', return_value=0) as call:
                self.assertTrue(client.install_key('BMI30-A'))
            command = call.call_args.args[0][-1]
            self.assertIn("hostname | tr", command)
            self.assertIn('ssh-ed25519 AAAA test-pc', command)
            self.assertIn('grep -qF', command)
            self.assertNotIn('PRIVATE KEY', command)

    def test_refresh_preserves_offline_alias_but_does_not_open_it(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as directory:
            root = Path(directory)
            (root / 'inventory.json').write_text(json.dumps({'BMI30-OLD': {'ip': '192.168.0.2'}}))
            with patch.object(client, 'ROOT', root), patch.object(client, 'SSH_ROOT', root), patch.object(
                client, 'discover', return_value={'BMI30-NEW': {'ip': '192.168.0.3'}}):
                current = client.refresh()
            self.assertEqual(list(current), ['BMI30-NEW'])
            self.assertIn('Host BMI30-OLD', (root / 'bmi30_config').read_text())


if __name__ == '__main__':
    unittest.main()
