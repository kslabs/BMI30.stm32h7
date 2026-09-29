import struct
import unittest
from unittest.mock import Mock
from vendor_relay import read_status, set_enabled, set_test, trigger, MAX_DURATION_MS


def reply(enabled=0, active=0, pin=0, mode=0, remaining=0):
    return b'RLY1' + bytes([1, enabled, active, pin, mode, 0, 0, 0]) + struct.pack('<II', remaining, MAX_DURATION_MS)


class RelayProtocolTests(unittest.TestCase):
    def test_status_modes_and_gpio_feedback(self):
        for raw, mode in [(reply(), 0), (reply(1, 1, 1, 1, 180000), 1),
                          (reply(1, 1, 1, 2, 0xFFFFFFFF), 2),
                          (reply(1, 1, 0, 1, 100), 1)]:
            dev = Mock()
            dev.ctrl_transfer.return_value = raw
            state = read_status(dev)
            self.assertEqual(state['mode'], mode)
            self.assertEqual(state['pc1'], raw[7])
            dev.ctrl_transfer.assert_called_once_with(0xC0, 0x4C, 0, 0, 20, timeout=500)

    def test_invalid_status_rejected(self):
        valid = reply()
        for raw in [b'', valid[:-1], valid+b'\0', b'BAD!'+valid[4:],
                    valid[:4]+b'\2'+valid[5:], reply(0, 1, 1, 1, 100),
                    reply(1, 0, 0, 1, 100), reply(1, 1, 1, 2, 100),
                    reply(1, 0, 0, 0, 100), reply(pin=2)]:
            dev = Mock()
            dev.ctrl_transfer.return_value = raw
            with self.subTest(raw=raw), self.assertRaises(RuntimeError):
                read_status(dev)

    def test_duration_wire_format_including_long_test(self):
        dev = Mock()
        dev.ctrl_transfer.return_value = 4
        for ms in [0, 1, 1600, 65536, 180000, MAX_DURATION_MS]:
            trigger(dev, ms)
            dev.ctrl_transfer.assert_called_with(0x40, 0x4B, 0, 0, struct.pack('<I', ms), timeout=500)

    def test_flags_and_invalid_inputs(self):
        dev = Mock()
        dev.ctrl_transfer.return_value = 0
        for setter, opcode in [(set_enabled, 0x4A), (set_test, 0x4D)]:
            for value in (0, 1):
                setter(dev, value)
                dev.ctrl_transfer.assert_called_with(0x40, opcode, value, 0, None, timeout=500)
            for value in (-1, 2, '1', 0.5):
                with self.assertRaises(ValueError):
                    setter(dev, value)
        dev.reset_mock()
        for ms in (-1, MAX_DURATION_MS+1, 1.5, '100'):
            with self.assertRaises(ValueError):
                trigger(dev, ms)
        dev.ctrl_transfer.assert_not_called()

    def test_incomplete_transfers_rejected(self):
        dev = Mock()
        dev.ctrl_transfer.return_value = 1
        for function, value in [(set_enabled, 1), (set_test, 1), (trigger, 180000)]:
            with self.assertRaises(RuntimeError):
                function(dev, value)


if __name__ == '__main__':
    unittest.main()
