import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import manager


class StrictBooleanSafetyTests(unittest.TestCase):
    def test_only_boolean_true_enables_safety_sensitive_options(self):
        self.assertTrue(manager.explicitly_enabled(True))
        for value in ("true", "yes", "1", 1, ["true"], {"enabled": True}):
            with self.subTest(value=value):
                self.assertFalse(manager.explicitly_enabled(value))

    def test_only_boolean_false_disables_preview_safeguard(self):
        self.assertTrue(manager.explicitly_disabled(False))
        for value in ("false", "no", "0", 0, None, [], {}):
            with self.subTest(value=value):
                self.assertFalse(manager.explicitly_disabled(value))


if __name__ == "__main__":
    unittest.main()
