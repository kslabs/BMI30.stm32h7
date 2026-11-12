import base64,contextlib,os,pathlib,tempfile,unittest
import lab_access

@unittest.skipIf(os.name=='nt','Requires Linux ownership')
class LabAccessTests(unittest.TestCase):
    @contextlib.contextmanager
    def fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory);(root/'etc').mkdir();(root/'home/techaid').mkdir(parents=True)
            (root/'etc/passwd').write_text('techaid:x:1000:1000::/home/techaid:/bin/bash\n')
            key='ssh-ed25519 '+base64.b64encode(b'\0\0\0\x0bssh-ed25519\0\0\0\x20'+b'K'*32).decode()
            cfg={'physical_serial':'abdd2dbc9775fbae','root_partuuid':'d3ab9668-02','public_key':key}
            yield root,cfg
    def test_restore_missing_authorized_key_and_permissions(self):
        with self.fixture() as (root,cfg):
            result=lab_access.restore(root,cfg,cfg['physical_serial'],'/dev/sda2',cfg['root_partuuid'])
            target=root/'home/techaid/.ssh/authorized_keys'
            self.assertTrue(result['key_added']);self.assertFalse(result['emmc_written'])
            self.assertEqual(target.stat().st_uid,1000);self.assertEqual(target.stat().st_mode&0o777,0o600)
            self.assertIn(cfg['public_key'],target.read_text())
    def test_existing_keys_are_retained_and_repeat_is_idempotent(self):
        with self.fixture() as (root,cfg):
            ssh=root/'home/techaid/.ssh';ssh.mkdir();target=ssh/'authorized_keys';target.write_text('# existing\nssh-rsa existing-public-key\n')
            lab_access.restore(root,cfg,cfg['physical_serial'],'/dev/sda2',cfg['root_partuuid'])
            before=target.read_text();r=lab_access.restore(root,cfg,cfg['physical_serial'],'/dev/sda2',cfg['root_partuuid'])
            self.assertFalse(r['key_added']);self.assertEqual(target.read_text(),before)
    def test_other_board_and_media_refused_before_writes(self):
        with self.fixture() as (root,cfg):
            for serial,source,part in [('490df72e29805c7c','/dev/sda2',cfg['root_partuuid']),
                (cfg['physical_serial'],'/dev/mmcblk0p2',cfg['root_partuuid']),
                (cfg['physical_serial'],'/dev/sda2','other-02')]:
                with self.assertRaises(RuntimeError):lab_access.restore(root,cfg,serial,source,part)
            self.assertFalse((root/'home/techaid/.ssh').exists())
    def test_linked_authorized_keys_refused(self):
        with self.fixture() as (root,cfg):
            ssh=root/'home/techaid/.ssh';ssh.mkdir();other=root/'other';other.write_text('unchanged')
            (ssh/'authorized_keys').symlink_to(other)
            with self.assertRaises(RuntimeError):lab_access.restore(root,cfg,cfg['physical_serial'],'/dev/sda2',cfg['root_partuuid'])
            self.assertEqual(other.read_text(),'unchanged')
    def test_malformed_public_key_refused(self):
        with self.fixture() as (root,cfg):
            cfg['public_key']='ssh-ed25519 aW52YWxpZA=='
            with self.assertRaises(RuntimeError):lab_access.restore(root,cfg,cfg['physical_serial'],'/dev/sda2',cfg['root_partuuid'])
            self.assertFalse((root/'home/techaid/.ssh').exists())

if __name__=='__main__':unittest.main()
