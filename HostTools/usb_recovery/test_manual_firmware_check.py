import ast,json,pathlib,threading,unittest
from unittest.mock import Mock
from patch_portal import patch as usb_patch
from patch_firmware_check import patch,HELPER
ROOT=pathlib.Path(__file__).resolve().parents[2]

class ManualCheckTests(unittest.TestCase):
    def context(self,busy=False):
        state={'checking':False,'next_check_at':4102444800,'cloud':{'version':'old'}}
        context={'_firmware_operation_busy':lambda:busy,'_FIRMWARE_CONTROL_LOCK':threading.RLock(),
                 '_FIRMWARE_CONTROL_STATE':state,'_FIRMWARE_CONTROL_WAKE':Mock()}
        exec(HELPER,context);return context,state
    def test_manual_request_wakes_checker_despite_hourly_future_deadline(self):
        context,state=self.context();result,status=context['request_manual_firmware_check']()
        self.assertEqual(status,202);self.assertTrue(result['ok']);self.assertTrue(state['checking'])
        context['_FIRMWARE_CONTROL_WAKE'].set.assert_called_once()
    def test_repeated_clicks_coalesce_and_do_not_start_firmware_installation(self):
        context,state=self.context()
        for _ in range(4):context['request_manual_firmware_check']()
        context['_FIRMWARE_CONTROL_WAKE'].set.assert_called_once()
        self.assertNotIn('launch_firmware_operation',context)
    def test_check_during_update_is_refused(self):
        context,state=self.context(True);result,status=context['request_manual_firmware_check']()
        self.assertEqual(status,409);self.assertFalse(result['ok']);self.assertFalse(state['checking'])
        context['_FIRMWARE_CONTROL_WAKE'].set.assert_not_called()
    def test_patch_keeps_existing_update_actions_and_requires_authenticated_post(self):
        source=usb_patch((ROOT/'.tmp/usb-recovery-20261028/M02-portal-before.py').read_text(encoding='utf-8'))
        candidate=patch(source);self.assertEqual(candidate,patch(candidate));ast.parse(candidate)
        self.assertIn('id="firmware-check-btn"',candidate)
        self.assertIn('X-BMI30-Firmware-Check',candidate)
        self.assertIn('if path in {"/api/firmware-update", "/api/firmware-rollback"}',candidate)
        self.assertIn("bmi30-firmware-manual-state",candidate)

if __name__=='__main__':unittest.main()
