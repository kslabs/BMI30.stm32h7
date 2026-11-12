"""Reloaded copy status must identify its actual destination without plan secrets."""
import json,pathlib,tempfile,unittest
from unittest.mock import patch
import usb_recovery_backend as backend
from usb_recovery_common import write_json

class OperationTargetTests(unittest.TestCase):
    def test_reloaded_job_exposes_only_destination_metadata_from_its_saved_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory);ident='a'*32;job=root/'jobs'/ident
            write_json(root/'latest.json',{'id':ident,'unit':'fixture-only'})
            write_json(job/'plan.json',{'target_path':'/dev/test-only','model':'TEST USB','serial':'TEST SERIAL','size':32000000000,
                                       'confirmation':'must-stay-private','authorized':True})
            for stage in ('queued','copying','failed','complete'):
                write_json(job/'status.json',{'stage':stage,'progress':50})
                with patch.object(backend,'STATE',root),patch.object(backend.subprocess,'run',return_value=type('Result',(),{'stdout':'active\n'})()):
                    value=backend.latest()
                self.assertEqual(value['target_path'],'/dev/test-only')
                self.assertEqual(value['target_model'],'TEST USB')
                self.assertEqual(value['target_serial'],'TEST SERIAL')
                self.assertEqual(value['target_size'],32000000000)
                self.assertNotIn('confirmation',json.dumps(value))
                self.assertNotIn('must-stay-private',json.dumps(value))
                self.assertNotIn('authorized',value)

if __name__=='__main__':unittest.main()
