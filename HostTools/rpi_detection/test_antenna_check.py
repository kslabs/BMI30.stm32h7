import unittest

from antenna_check import antenna_state


class AntennaTests(unittest.TestCase):
    def state(self, first, second):
        return antenna_state(f"205: op pd | {first} // ANT1/AON_GPIO5 = output\n"
                             f"206: op pd | {second} // ANT2/AON_GPIO6 = output\n")

    def test_external_selected_only_for_complementary_levels(self):
        self.assertTrue(self.state("lo", "hi")["external_selected"])
        for levels in (("hi", "lo"), ("hi", "hi"), ("lo", "lo")):
            self.assertFalse(self.state(*levels)["external_selected"])

    def test_missing_or_duplicate_signal_is_not_reported_as_success(self):
        one = "205: op pd | lo // ANT1/AON_GPIO5 = output\n"
        for text in ("", one, one + one):
            with self.assertRaises(ValueError):
                antenna_state(text)

    def test_matches_signal_names_not_global_gpio_numbers(self):
        result = antenna_state("7: op | lo // ANT1/AON_GPIO5 = output\n"
                               "8: op | hi // ANT2/AON_GPIO6 = output\n"
                               "9: op | hi // UNRELATED/GPIO9 = output\n")
        self.assertTrue(result["external_selected"])


if __name__ == "__main__":
    unittest.main()
