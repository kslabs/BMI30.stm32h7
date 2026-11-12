"""Regressions found by the first real removable-system boot/copy test."""
import itertools,os,pathlib,stat,subprocess,tempfile,unittest
from unittest.mock import patch
import usb_recovery_worker as w
import usb_recovery_adopt as a
from usb_recovery_common import read_json,write_json

class CopyTests(unittest.TestCase):
    def test_native_clone_cannot_replay_a_usb_update_without_its_excluded_backups(self):
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory)
            unit=root/'etc/systemd/system/bmi30-legacy-guard-test.service';unit.parent.mkdir(parents=True);unit.write_text('source guard')
            for name in ('var/lib/bmi30-legacy-menu/release/state.json','home/techaid/Documents/.bmi30_cloud_sync/server/rollback.json',
                         'home/techaid/Documents/host/bmi30_rollback.env','home/techaid/Documents/.bmi30_cloud_sync/rollback_state.env'):
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('source transaction')
            own=root/'var/lib/bmi30-agent/own-identity';own.parent.mkdir(parents=True);own.write_text('keep')
            marker=root/'var/lib/bmi30-portable-runtime.json';marker.write_text('own runtime')
            w.remove_copied_update_transactions(root)
            self.assertFalse(unit.exists());self.assertFalse((root/'var/lib/bmi30-legacy-menu').exists())
            self.assertFalse((root/'home/techaid/Documents/.bmi30_cloud_sync/server').exists())
            self.assertFalse((root/'home/techaid/Documents/host/bmi30_rollback.env').exists())
            self.assertEqual(own.read_text(),'keep');self.assertEqual(marker.read_text(),'own runtime')

    def test_usb_does_not_inherit_the_master_update_guard_or_its_boot_link(self):
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory);units=root/'etc/systemd/system';links=units/'multi-user.target.wants';links.mkdir(parents=True)
            name='bmi30-legacy-guard-2026-10-29-235959.service'
            (units/name).write_text('donor transaction\n');(links/name).symlink_to('../'+name)
            other=units/'bmi30-core.service';other.write_text('keep\n')
            w.remove_donor_rollback_units(root)
            self.assertFalse((units/name).exists());self.assertFalse((links/name).is_symlink());self.assertEqual(other.read_text(),'keep\n')
    def test_native_restore_keeps_configuration_owner_and_ssh_authorized_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            base=pathlib.Path(directory);source=base/'source';target=base/'target'
            cfg=source/'home/techaid/Documents/host/bmi30_config.json';cfg.parent.mkdir(parents=True);cfg.write_text('{"own":true}');cfg.chmod(0o600);os.chown(cfg,1000,1000)
            auth=source/'home/techaid/.ssh/authorized_keys';auth.parent.mkdir();auth.write_text('test-public-key\n');auth.chmod(0o600);os.chown(auth,1000,1000);os.chown(auth.parent,1000,1000);auth.parent.chmod(0o700)
            destination=target/cfg.relative_to(source)
            w.copy_live_configuration(cfg,destination);w.copy_native_login_access(target,source)
            self.assertEqual(destination.stat().st_uid,1000);self.assertEqual(destination.read_bytes(),cfg.read_bytes())
            saved=target/auth.relative_to(source)
            self.assertEqual(saved.stat().st_uid,1000);self.assertEqual(saved.read_bytes(),auth.read_bytes())
            self.assertEqual(stat.S_IMODE(saved.parent.stat().st_mode),0o700)
    def test_fresh_rsync_target_has_boot_mountpoints_after_both_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            base=pathlib.Path(directory);source=base/'source';target=base/'target'
            source.mkdir();target.mkdir()
            for name in ('dev','proc','sys','run','tmp','mnt','media','var/log','var/cache'):
                path=source/name;path.mkdir(parents=True,exist_ok=True);(path/'live-data').write_text('excluded')
            for _ in range(2):
                subprocess.run(['rsync','-a','--delete-delay']+['--exclude='+v for v in w.EXCLUDES]+[str(source)+'/',str(target)+'/'],check=True,capture_output=True)
            self.assertFalse((target/'dev').exists()) # reproduce the failed image
            w.prepare_system_directories(target)
            for name in ('dev','proc','sys','run','tmp','mnt','media','var/log','var/cache','var/tmp','boot/firmware'):
                path=target/name;self.assertTrue(path.is_dir(),name)
                self.assertFalse((path/'live-data').exists())
                self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o1777 if name in ('tmp','var/tmp') else 0o755)
                self.assertEqual(path.stat().st_uid,0)

    def test_progress_does_not_retreat_on_rsync_updates_second_pass_or_failure(self):
        class Process:
            stdout=type('Stream',(),{'fileno':lambda self:99})()
            def wait(self,**kwargs):return 0
            def poll(self):return 0
        with tempfile.TemporaryDirectory() as directory:
            job=pathlib.Path(directory);write_json(job/'plan.json',{'kind':'usb'})
            reports=[];real_stage=w.stage
            def stage(*args,**kwargs):
                real_stage(*args,**kwargs);reports.append(read_json(job/'status.json')['progress'])
            with patch.object(w,'stage',side_effect=stage),patch.object(w.subprocess,'Popen',return_value=Process()) as popen,patch.object(w.os,'read',side_effect=[b' 100 90%\r 120 20%\r 400 100%\r',b'',b' 20 1%\r 40 100%\r',b'']),patch.object(w.time,'monotonic',side_effect=itertools.count(2,2)):
                w.copy_root(job,job/'root',12,70)
                w.copy_root(job,job/'root',72,82)
                w.stage(job,'failed',0,'stopped')
            self.assertEqual(reports,sorted(reports))
            self.assertEqual(reports[-1],82)
            self.assertIn('--no-inc-recursive',popen.call_args.args[0])
            self.assertEqual(read_json(job/'status.json')['kind'],'usb')
            w.stage(job,'finishing',100,'sync')
            self.assertEqual(read_json(job/'status.json')['progress'],99)
            w.stage(job,'complete',100,'done')
            self.assertEqual(read_json(job/'status.json')['progress'],100)

    def test_personalization_keeps_machine_id_already_used_by_pid1(self):
        from test_emergency_boot import fixture,OTHER
        with fixture() as (snapshot,usb,image,commands):
            machine=usb/'etc/machine-id';machine.parent.mkdir(parents=True,exist_ok=True)
            machine.write_text('2'*32+'\n');before=machine.stat().st_ino
            a.personalize(image,OTHER,snapshot)
            self.assertEqual(machine.read_text(),'2'*32+'\n')
            self.assertEqual(machine.stat().st_ino,before)

if __name__=='__main__':unittest.main()
