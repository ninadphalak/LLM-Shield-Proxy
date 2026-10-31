"""Audit 2026-10: workflow hardening that is checked by reading the YAML.

- pr-review-workflow:fallback-review-runs-after-claude-write-check-refusal: `/review` counts only
  from OWNER/MEMBER/COLLABORATOR, in the job condition and again on both fallback steps.
- pr-review-workflow:claude-allowed-gh-pr-comment-body-file-disclosure: the review checkout keeps
  no credentials, and the model's comment tool cannot take a body from a file.
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
    args = _step(_review_job(), "Review")["with"]["claude_args"]
    assert "Bash(gh pr comment:*)" not in args
    assert "Bash(gh pr comment * --body *)" in args
    disallowed = next(line for line in args.splitlines() if line.startswith("--disallowedTools"))
    for flag in ("--body-file", "-F", "--editor", "-e", "--web", "-w"):
        assert flag in disallowed, flag
    # And the model is told so, since a deny rule it does not know about just fails the step.
    assert "use `--body-file`" in _step(_review_job(), "Review")["with"]["prompt"]


def test_prompt_linter_inputs_never_reach_the_script_text():
    action = yaml.safe_load((REPO_ROOT / ".github" / "actions" / "prompt-linter" / "action.yml").read_text(encoding="utf-8"))
    for step in action["runs"]["steps"]:
        if "run" in step:
            assert "${{" not in step["run"], step.get("name")
