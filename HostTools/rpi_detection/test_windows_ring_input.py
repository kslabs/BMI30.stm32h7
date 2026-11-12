"""The collector must never treat another input device as the selected ring."""
import unittest

from windows_ring_input import belongs_to_ring


class RingIdentityTests(unittest.TestCase):
    def test_matches_exact_mac_at_hid_path_boundary(self):
        self.assertTrue(belongs_to_ring({"path": r"\\?\HID#DEV_FF240126A04A&Col01#abc"}, "ff240126a04a"))
        self.assertTrue(belongs_to_ring({"path": r"\\?\HID#DEV_ff240126a04a#abc"}, "ff240126a04a"))

    def test_rejects_other_devices_and_mac_prefixes(self):
        for path in (r"\\?\HID#DEV_AA240126A04A&Col01#abc",
                     r"\\?\HID#DEV_FF240126A04A0&Col01#abc",
                     r"\\?\HID#VID_046D&PID_C31C#keyboard",
                     r"\\?\HID#SOMETHINGFF240126A04A&Col01#abc"):
            self.assertFalse(belongs_to_ring({"path": path}, "ff240126a04a"))

    def test_unknown_device_is_not_recorded(self):
        self.assertFalse(belongs_to_ring(None, "ff240126a04a"))


if __name__ == "__main__":
    unittest.main()
