#!/usr/bin/env python3
import pathlib
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "product-owner.yml"


class ProductOwnerWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow_text = WORKFLOW.read_text(encoding="utf-8")

    def test_untrusted_text_inputs_are_passed_through_environment(self):
        gather_step = self.workflow_text.split("- name: Gather product context", 1)[1].split("# ── Alex", 1)[0]

        self.assertIn("EXTRA_CONTEXT: ${{ inputs.extra_context }}", gather_step)
        self.assertIn("FEATURE_PROMPT: ${{ inputs.feature_prompt }}", gather_step)
        self.assertIn("DISCUSSION_BODY: ${{ github.event.discussion.body }}", gather_step)
        self.assertIn('echo "$EXTRA_CONTEXT"', gather_step)
        self.assertIn('echo "$FEATURE_PROMPT"', gather_step)
        self.assertNotIn('echo "${{ inputs.extra_context }}"', gather_step)
        self.assertNotIn('echo "${{ inputs.feature_prompt }}"', gather_step)


if __name__ == "__main__":
    unittest.main(verbosity=2)