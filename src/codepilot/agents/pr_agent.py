from __future__ import annotations

from dataclasses import asdict, dataclass

from src.codepilot.models import AgentResult, Issue


@dataclass
class PullRequestDraft:
    branch: str
    title: str
    body: str
    labels: list[str]


class PRAgent:
    def build_branch_name(self, issue: Issue) -> str:
        slug = "-".join(issue.title.lower().split())[:40]
        return f"codepilot/issue-{issue.issue_id}-{slug}"

    def run(self, issue: Issue, files_changed: list[str], test_output: str) -> AgentResult:
        branch = self.build_branch_name(issue)
        title = f"[CodePilot] {issue.title}"
        body = (
            f"## Summary\nFix for issue #{issue.issue_id}.\n\n"
            "## Implementation\n"
            f"- Updated files: {', '.join(files_changed) if files_changed else 'none'}\n"
            "- Followed assignment MVP flow\n\n"
            "## Test Results\n"
            f"```\n{test_output[:1500]}\n```\n"
        )
        draft = PullRequestDraft(
            branch=branch,
            title=title,
            body=body,
            labels=["codepilot-generated", "needs-review"],
        )
        return AgentResult(ok=True, message="PR draft created", payload={"pr_draft": asdict(draft)})
