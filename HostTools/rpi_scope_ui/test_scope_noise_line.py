"""Saved reference-noise scalars and event time must never follow live state."""
import json
from types import SimpleNamespace
import unittest

import numpy as np

import test_autofreeze_pair as regression
from bmi30_common_mode import review_window


class SavedNoiseTests(unittest.TestCase):
    def test_both_channels_keep_immutable_reference_noise_and_event_timestamp(self):
        regression.FrameSerializationTests.setUpClass()
        for ch in (0,1):
            test, frames = regression.AutoFreezePairTests().fixture()
            test.scope._det_noise_avg0 = 12.25
            test.scope._det_noise_avg1 = 25.5
            block = tuple(np.full(200,32000+i) for i in range(4))
            test.send(24,ch,candidate_current_in=block[ch*2],candidate_previous_in=block[ch*2+1],
                candidate_frame_block_in=block,candidate_product_block_in=(regression.pulse(24),regression.pulse(22)))
            captured = frames[-1]['noise_levels']
            self.assertEqual(captured[1-ch],25.5 if ch==0 else 12.25)
            test.scope._det_noise_avg0 = test.scope._det_noise_avg1 = 999999.
            saved = review_window(frames[-1],frames)
            saved.update(fire_channel=ch,fire_seq=7,fire_timestamp=123.,
                         snapshot={'seq':7,'timestamp':123.,'noise_avg':captured[ch]})
            scope = SimpleNamespace(base_buf_len=200,max_samples=200,last_frame_t=999.,
                num_group=SimpleNamespace(checkedId=lambda:6),xcorr_norm_enabled=False,
                _det_noise_avg0=999999.,_det_noise_avg1=999999.)
            setattr(scope,f'_web_det_fire_frames{ch}',{7:saved})
            api = regression.FrameSerializationTests.api_class.__new__(regression.FrameSerializationTests.api_class)
            api.scope = scope
            query = {f'fire_seq{ch}':7}
            frame = api.frame(**query)
            ref = frame[f'reference_snapshot{1-ch}']
            self.assertEqual(ref['noise_avg'],captured[1-ch])
            self.assertEqual(ref['event_timestamp'],123.)
            self.assertEqual(frame['event_snapshot']['noise_avg'],captured[ch])
            raw = api.frame_binary(**query)
            length = int.from_bytes(raw[4:8],'little')
            header = json.loads(raw[8:8+length])
            self.assertEqual(header[f'reference_snapshot{1-ch}'],ref)


if __name__=='__main__':
    unittest.main()
