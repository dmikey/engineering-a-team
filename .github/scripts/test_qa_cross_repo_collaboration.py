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


class QaRobustnessTests(unittest.TestCase):
    """Verify improvements added to reduce failures and improve run efficiency."""

    @classmethod
    def setUpClass(cls):
        cls.workflow_text = QA_WORKFLOW.read_text(encoding="utf-8")

    def test_job_has_timeout(self):
        """The qa-review job must declare a timeout to prevent runaway runs."""
        self.assertIn("timeout-minutes:", self.workflow_text)

    def test_checkout_uses_conditional_fetch_depth(self):
        """Non-PR triggers use a shallow clone to speed up checkout."""
        self.assertIn("fetch-depth:", self.workflow_text)
        # Conditional expression should reference github.event_name
        self.assertIn("github.event_name == 'pull_request'", self.workflow_text)

    def test_diff_content_capped_for_token_safety(self):
        """DIFF_CONTENT must be capped at ≤200 lines to avoid model token-limit failures."""
        import re
        # Extract the head limit used for DIFF_CONTENT
        match = re.search(r"DIFF_CONTENT=.*head -(\d+)", self.workflow_text)
        self.assertIsNotNone(match, "DIFF_CONTENT head limit not found in workflow")
        limit = int(match.group(1))
        self.assertLessEqual(limit, 200, f"DIFF_CONTENT head limit {limit} exceeds 200")

    def test_issue_creation_step_has_continue_on_error(self):
        """The issue-creation step must not fail the overall workflow on error."""
        # Find the 'Open issue for serious findings' step and verify it has continue-on-error
        self.assertIn("continue-on-error: true", self.workflow_text)

    def test_security_assessment_covers_all_owasp_top_10(self):
        """The security prompt must reference all 10 OWASP Top 10 categories."""
        for category in ("A01", "A02", "A03", "A04", "A05", "A06", "A07", "A08", "A09", "A10"):
            self.assertIn(
                category,
                self.workflow_text,
                f"OWASP category {category} not found in qa-engineer.yml security prompt",
            )

    def test_validate_step_gives_actionable_error_on_empty_response(self):
        """Validate step must include guidance about token limits in the error message."""
        self.assertIn("AGENT_MAX_TOKENS", self.workflow_text)
        self.assertIn("Empty response received from model", self.workflow_text)

    def test_validate_step_gives_actionable_error_on_model_failure(self):
        """Validate step must include guidance about token and API issues."""
        self.assertIn("MODELS_TOKEN", self.workflow_text)
        self.assertIn("Model call failed", self.workflow_text)

    def test_model_failures_fall_back_to_manual_review_handoff(self):
        """Model failures should degrade gracefully instead of failing the workflow."""
        self.assertIn(
            "Falling back to a manual-review handoff so the workflow can still complete.",
            self.workflow_text,
        )
        self.assertIn("response<<QA_RESP_EOF", self.workflow_text)
        self.assertIn("Original model error:", self.workflow_text)

    def test_downstream_steps_use_normalized_qa_response(self):
        """Posting and issue-creation steps must use the normalized fallback-aware response."""
        self.assertIn("steps.qa_response.outputs.response", self.workflow_text)
        self.assertIn("**Recommendation**: [🔄 REQUEST CHANGES]", self.workflow_text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
