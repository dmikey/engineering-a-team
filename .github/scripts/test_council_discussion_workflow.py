#!/usr/bin/env python3
import pathlib
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "council-discussion.yml"


class CouncilDiscussionWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow_text = WORKFLOW.read_text(encoding="utf-8")

    def test_discussion_comments_trigger_council(self):
        self.assertIn("discussion_comment:", self.workflow_text)
        self.assertIn("types: [created]", self.workflow_text)
        self.assertIn("github.event_name == 'discussion_comment'", self.workflow_text)
        self.assertIn("github.actor != 'github-actions[bot]'", self.workflow_text)

    def test_discussion_comment_context_is_passed_through_environment(self):
        resolve_step = self.workflow_text.split("- name: Resolve topic and context", 1)[1].split("# Gather repository context", 1)[0]

        self.assertIn("DISCUSSION_COMMENT_BODY: ${{ github.event.comment.body }}", resolve_step)
        self.assertIn("DISCUSSION_COMMENT_NODE_ID: ${{ github.event.comment.node_id }}", resolve_step)
        self.assertIn("CONTEXT=$(printf", resolve_step)
        self.assertIn("A user requested council input", resolve_step)
        self.assertNotIn("${{ github.event.comment.body }}", resolve_step.split("run: |", 1)[1])

    def test_discussion_comment_reply_uses_graphql_reply_to_id(self):
        self.assertIn("Reply to originating discussion comment", self.workflow_text)
        self.assertIn("addDiscussionComment", self.workflow_text)
        self.assertIn("replyToId:$replyToId", self.workflow_text)
        self.assertIn("steps.resolve.outputs.discussion_reply_id", self.workflow_text)

    def test_discussion_replies_do_not_create_decision_artifacts_by_default(self):
        self.assertIn("MODE=\"discussion-reply\"", self.workflow_text)
        self.assertIn("if: steps.resolve.outputs.mode != 'discussion-reply'", self.workflow_text)
        self.assertIn("No separate decision artifact was created", self.workflow_text)
        self.assertIn("/council decide", self.workflow_text)

    def test_discussion_comment_reply_is_concise_not_raw_moderator_dump(self):
        reply_step = self.workflow_text.split("- name: Reply to originating discussion comment", 1)[1]

        self.assertIn("OUTCOME=$(printf", reply_step)
        self.assertIn("Full council record", reply_step)
        self.assertIn("Keeping this reply short", reply_step)
        self.assertNotIn("head -40", reply_step)

    def test_workflow_dispatch_can_backfill_existing_discussion_comment(self):
        self.assertIn("discussion_id:", self.workflow_text)
        self.assertIn("discussion_reply_to_id:", self.workflow_text)
        self.assertIn("INPUT_DISCUSSION_ID: ${{ inputs.discussion_id }}", self.workflow_text)
        self.assertIn("DISCUSSION_REPLY_ID=\"$INPUT_DISCUSSION_ID\"", self.workflow_text)
        self.assertIn("if: steps.resolve.outputs.discussion_reply_id != ''", self.workflow_text)


if __name__ == "__main__":
    unittest.main(verbosity=2)