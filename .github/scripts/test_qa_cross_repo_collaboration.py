#!/usr/bin/env python3
import pathlib
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
QA_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "qa-engineer.yml"


class QaCrossRepoCollaborationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow_text = QA_WORKFLOW.read_text(encoding="utf-8")

    def test_declares_collaboration_repository_variable(self):
        self.assertIn("QA_COLLAB_REPOSITORIES", self.workflow_text)

    def test_adds_cross_repository_issue_context_section(self):
        self.assertIn("## Cross-Repository Issue Context", self.workflow_text)
        self.assertIn("gh issue list \\", self.workflow_text)
        self.assertIn("--repo \"$TARGET_REPO\" \\", self.workflow_text)

    def test_mirrors_serious_findings_to_configured_repositories(self):
        self.assertIn("if [ -n \"$QA_COLLAB_REPOSITORIES\" ]; then", self.workflow_text)
        self.assertIn("gh issue create \\", self.workflow_text)
        self.assertIn("--repo \"$TARGET_REPO\" \\", self.workflow_text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
