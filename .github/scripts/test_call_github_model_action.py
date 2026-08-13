#!/usr/bin/env python3
import pathlib
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
MODEL_ACTION = REPO_ROOT / ".github" / "actions" / "call-github-model" / "action.yml"


class CallGithubModelActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.action_text = MODEL_ACTION.read_text(encoding="utf-8")

    def test_uses_copilot_cli_token(self):
        self.assertIn("COPILOT_GITHUB_TOKEN", self.action_text)
        self.assertIn("gh copilot", self.action_text)

    def test_disables_tools_for_inference_only_calls(self):
        self.assertIn("--disable-builtin-mcps", self.action_text)
        self.assertIn("--deny-tool '*'", self.action_text)

    def test_handles_cli_and_empty_response_errors(self):
        self.assertIn("Copilot call failed", self.action_text)
        self.assertIn("empty response from model", self.action_text)

    def test_has_no_retired_models_endpoint(self):
        self.assertNotIn("models.github.ai", self.action_text)
        self.assertNotIn("curl ", self.action_text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
