import itertools,os,pathlib,subprocess,sys,tempfile,time,unittest
from unittest.mock import patch
import usb_recovery_finish as finish
import usb_recovery_progress as progress
import usb_recovery_worker as worker
from usb_recovery_common import Refused,read_json,write_json

class FinishTests(unittest.TestCase):
    @unittest.skipIf(os.name=='nt','Run on the Linux target in a private fixture')
    def test_sync_longer_than_the_old_thirty_second_limit_completes(self):
        beats=[];started=time.monotonic()
        finish.run_step([sys.executable,'-c','import time;time.sleep(31.2)'],'sync-root',40,beats.append)
        self.assertGreater(time.monotonic()-started,30);self.assertGreaterEqual(len(beats),10)

    def test_deadline_refuses_success_and_identifies_the_failed_operation(self):
        with self.assertRaises(finish.FinishTimeout) as error:
            finish.run_step([sys.executable,'-c','import time;time.sleep(5)'],'sync-root',.07,lambda value:None)
        self.assertEqual(error.exception.operation,'sync-root');self.assertEqual(error.exception.timeout_s,.07)

    def test_all_four_operations_are_required_before_finishing_returns(self):
        calls=[];messages=[]
        def run(args,operation,timeout,heartbeat):calls.append((args[0],operation,timeout));heartbeat(3)
        with patch.object(finish,'run_step',side_effect=run),patch.object(finish.os.path,'ismount',return_value=False):
            finish.finish(pathlib.Path('/fixture'),'/fixture/boot','/fixture/root',lambda *args,**kwargs:messages.append((args,kwargs)))
        self.assertEqual([x[1] for x in calls],['sync-boot','sync-root','unmount-boot','unmount-root'])
        self.assertGreater(calls[1][2],30)
        self.assertTrue(all(args[1]=='finishing' and args[2]<100 for args,kwargs in messages))

    def test_nonzero_or_still_mounted_is_never_reported_ready(self):
        with self.assertRaises(Refused):
            finish.run_step([sys.executable,'-c','raise SystemExit(1)'],'unmount-root',1,lambda value:None)
        with patch.object(finish,'run_step'),patch.object(finish.os.path,'ismount',return_value=True):
            with self.assertRaises(Refused):finish.finish(None,'/boot-fixture','/root-fixture',lambda *args,**kwargs:None)

class MetricsTests(unittest.TestCase):
    def test_elapsed_and_whole_operation_speed_ignore_wall_clock_adjustment(self):
        start=progress.fields({},now_mono=10,now_wall=1000)
        later=progress.fields(start,bytes_copied=1000,now_mono=15,now_wall=5)
        self.assertEqual(later['elapsed_s'],5);self.assertEqual(later['average_bytes_per_s'],200)
        self.assertEqual(later['started_at'],1000)
        end=progress.fields(later,finished=True,now_mono=20,now_wall=5000)
        self.assertEqual(end['elapsed_s'],10);self.assertEqual(end['finished_at'],5000)

    def test_delta_pass_counts_only_transferred_files_not_all_rechecked_data(self):
        class Process:
            stdout=type('Stream',(),{'fileno':lambda self:99})()
            def wait(self,**kwargs):return 0
            def poll(self):return 0
        with tempfile.TemporaryDirectory() as directory:
            job=pathlib.Path(directory);write_json(job/'plan.json',{'kind':'usb'})
            chunks=[b' 1,000,000 100%\rTotal transferred file size: 1,000,000 bytes\n',b'',
                    b' 1,000,000,000 100%\rTotal transferred file size: 3,000 bytes\n',b'']
            with patch.object(worker.subprocess,'Popen',return_value=Process()),patch.object(worker.os,'read',side_effect=chunks),patch.object(worker.time,'monotonic',side_effect=itertools.count(1,1)):
                worker.copy_root(job,job/'root',12,70);worker.copy_root(job,job/'root',72,82)
            self.assertEqual(read_json(job/'status.json')['bytes_copied'],1_003_000)

if __name__=='__main__':unittest.main()
