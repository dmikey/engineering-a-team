#!/usr/bin/env python3
import pathlib
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
MODEL_ACTION = REPO_ROOT / ".github" / "actions" / "call-github-model" / "action.yml"


class CallGithubModelActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.action_text = MODEL_ACTION.read_text(encoding="utf-8")

    def test_authorization_header_uses_api_token(self):
        self.assertIn("Authorization: " + "Bearer " + "$" + "API_TOKEN", self.action_text)

    def test_does_not_retry_all_errors(self):
        self.assertNotIn("--retry-all-errors", self.action_text)
        self.assertIn("--retry-connrefused", self.action_text)

    def test_handles_transport_and_empty_body_errors(self):
        self.assertIn("Network error while calling GitHub Models API", self.action_text)
        self.assertIn("Empty response body from GitHub Models API", self.action_text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
