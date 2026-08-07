import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".github" / "scripts"))

from task_assignment_dispatch_guard import should_auto_dispatch


class TaskDispatchGuardTests(unittest.TestCase):
    def test_allows_high_confidence_dispatch_when_enabled(self):
        allowed, reason = should_auto_dispatch("HIGH", True, 0, 2)
        self.assertTrue(allowed)
        self.assertIn("allowed", reason.lower())

    def test_blocks_when_auto_dispatch_is_disabled(self):
        allowed, reason = should_auto_dispatch("HIGH", False, 0, 2)
        self.assertFalse(allowed)
        self.assertIn("disabled", reason.lower())

    def test_blocks_when_confidence_is_not_high(self):
        allowed, reason = should_auto_dispatch("MEDIUM", True, 0, 2)
        self.assertFalse(allowed)
        self.assertIn("confidence", reason.lower())

    def test_blocks_when_dispatch_limit_is_reached(self):
        allowed, reason = should_auto_dispatch("HIGH", True, 2, 2)
        self.assertFalse(allowed)
        self.assertIn("limit", reason.lower())


if __name__ == "__main__":
    unittest.main()
