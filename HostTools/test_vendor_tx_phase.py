"""Host protocol checks; no USB hardware or PyUSB import required."""

import unittest
from unittest.mock import Mock

from vendor_tx_phase import read_phase, set_phase, wait_applied


# TX1 antiphase, TX2 in phase, enabled stream, logical sync HIGH.
APPLIED = b"TXP1\x01\x01\x00\x01\x00\x07\x01\x01\x01\x01\x00\x00"
PENDING = b"TXP1\x01\x01\x00\x00\x00\x0f\x01\x00\x01\x01\x00\x00"


class TxPhaseProtocolTests(unittest.TestCase):
    def test_readback_fields_and_control_request(self):
        dev = Mock()
        dev.ctrl_transfer.return_value = APPLIED
        state = read_phase(dev)
        dev.ctrl_transfer.assert_called_once_with(0xC0, 0x49, 0, 0, 16, timeout=500)
        self.assertEqual(state["requested"], [1, 0])
        self.assertEqual(state["applied"], [1, 0])
        self.assertTrue(state["tx_requested"] and state["tx_enabled"] and state["streaming"])
        self.assertFalse(state["pending"])
        self.assertEqual([state[k] for k in ("sync_phase", "pa1", "pa2", "pc7")], [1]*4)

    def test_bad_responses_rejected(self):
        for raw in (b"", APPLIED[:-1], APPLIED + b"\0", b"BAD!" + APPLIED[4:],
                    b"TXP1\x02" + APPLIED[5:], APPLIED[:7] + b"\x02" + APPLIED[8:]):
            with self.subTest(raw=raw):
                dev = Mock()
                dev.ctrl_transfer.return_value = raw
                with self.assertRaises(RuntimeError):
                    read_phase(dev)

    def test_absolute_commands_for_both_channels(self):
        dev = Mock()
        dev.ctrl_transfer.return_value = 0
        for ch, cmd in ((1, 0x47), (2, 0x48)):
            for phase in (0, 1):
                set_phase(dev, ch, phase)
                dev.ctrl_transfer.assert_called_with(0x40, cmd, phase, 0, None, timeout=500)

    def test_invalid_set_does_not_send_usb(self):
        dev = Mock()
        for ch, phase in ((0, 0), (3, 1), (1, 2), (2, -1), (1, 180)):
            with self.assertRaises(ValueError):
                set_phase(dev, ch, phase)
        dev.ctrl_transfer.assert_not_called()

    def test_unexpected_out_result_rejected(self):
        dev = Mock()
        dev.ctrl_transfer.return_value = 1
        with self.assertRaises(RuntimeError):
            set_phase(dev, 1, 1)

    def test_waits_for_applied_phase(self):
        dev = Mock()
        dev.ctrl_transfer.side_effect = [PENDING, APPLIED]
        self.assertEqual(wait_applied(dev, [1, 0])["applied"], [1, 0])
        self.assertEqual(dev.ctrl_transfer.call_count, 2)

    def test_pending_timeout(self):
        dev = Mock()
        dev.ctrl_transfer.return_value = PENDING
        with self.assertRaises(RuntimeError):
            wait_applied(dev, [1, 0], timeout_s=0)

    def test_mismatched_request_timeout(self):
        dev = Mock()
        dev.ctrl_transfer.return_value = APPLIED
        with self.assertRaises(RuntimeError):
            wait_applied(dev, [0, 1], timeout_s=0)


if __name__ == "__main__":
    unittest.main()
