import json
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "autonomous-heartbeat.sh"


class AutonomousHeartbeatMergeTests(unittest.TestCase):
    def test_pr_verdict_uses_qa_approval_for_merge(self):
        payload = {
            "reviewDecision": "REVIEW_REQUIRED",
            "comments": [
                {
                    "body": "## QA Review — PR #12\n\n**Recommendation**: [✅ APPROVE]",
                    "createdAt": "2026-07-31T00:00:00Z",
                }
            ],
            "commits": [
                {"committedDate": "2026-07-30T23:59:00Z"},
            ],
        }

        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as handle:
            json.dump(payload, handle)
            temp_path = handle.name

        try:
            result = subprocess.run(
                ["bash", str(SCRIPT), "--print-pr-verdict", temp_path],
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertEqual(result.stdout.strip(), "approve_and_merge")
        finally:
            Path(temp_path).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
