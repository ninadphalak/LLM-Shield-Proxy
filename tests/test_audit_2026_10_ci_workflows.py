"""Audit 2026-10: workflow hardening that is checked by reading the YAML.

- pr-review-workflow:fallback-review-runs-after-claude-write-check-refusal: `/review` counts only
  from OWNER/MEMBER/COLLABORATOR, in the job condition and again on both fallback steps.
- pr-review-workflow:claude-allowed-gh-pr-comment-body-file-disclosure: the review checkout keeps
  no credentials, and the model has no comment command at all: a fixed workflow step posts its
  final message (flag deny patterns refused honest summaries that mention ` -e` or `--body-file`).
- The prompt-linter composite action passes its inputs through the environment, not the script.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
GATE = """contains(fromJSON('["OWNER", "MEMBER", "COLLABORATOR"]'), github.event.comment.author_association)"""


def _review_job():
    return yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "pr_review.yml").read_text(encoding="utf-8"))["jobs"]["review"]


def _step(job, name):
    return next(step for step in job["steps"] if step.get("name") == name)


def test_review_comment_trigger_requires_a_trusted_association():
    job = _review_job()
    comment_branch = job["if"].split("||")[0]
    assert "startsWith(github.event.comment.body, '/review')" in comment_branch
    assert GATE in comment_branch


def test_fallback_steps_repeat_the_commenter_gate():
    job = _review_job()
    for name in ("Configure fallback credentials", "Fallback review"):
        condition = _step(job, name)["if"]
        assert GATE in condition and "github.event_name != 'issue_comment'" in condition, name


def test_review_checkout_does_not_persist_credentials():
    assert _step(_review_job(), "Checkout")["with"]["persist-credentials"] is False


def test_review_comment_tool_cannot_read_a_file_into_the_body():
    review = _step(_review_job(), "Review")["with"]
    args = review["claude_args"]
    allowed = next(line for line in args.splitlines() if line.startswith("--allowedTools"))
    disallowed = next(line for line in args.splitlines() if line.startswith("--disallowedTools"))
    # No comment command for the model, and the whole command is denied as a backstop.
    assert "gh pr comment" not in allowed
    assert "Bash(gh pr comment:*)" in disallowed
    # No flag-substring deny patterns that would refuse an honest summary's text.
    assert "-e*" not in disallowed and "--body-file" not in disallowed
    # And the model is told to end with the summary rather than post it.
    assert "final message" in review["prompt"] and "Verdict: MERGEABLE" in review["prompt"]


def test_review_summary_is_posted_by_a_fixed_step():
    job = _review_job()
    names = [step.get("name") for step in job["steps"]]
    assert names.index("Post review summary") == names.index("Review") + 1
    step = _step(job, "Post review summary")
    assert "steps.review.outcome == 'success'" in step["if"]
    assert step["env"]["EXECUTION_FILE"] == "${{ steps.review.outputs.execution_file }}"
    # Workflow values reach the script through the environment only.
    assert "${{" not in step["run"]
    assert 'gh pr comment "$PR_NUMBER" --body-file "$summary"' in step["run"]
    assert '"$RUNNER_TEMP/review-summary.md"' in step["run"]


def test_prompt_linter_inputs_never_reach_the_script_text():
    action = yaml.safe_load((REPO_ROOT / ".github" / "actions" / "prompt-linter" / "action.yml").read_text(encoding="utf-8"))
    for step in action["runs"]["steps"]:
        if "run" in step:
            assert "${{" not in step["run"], step.get("name")
