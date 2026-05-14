from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from src.codepilot.agents.coder import CoderAgent
from src.codepilot.agents.pr_agent import PRAgent
from src.codepilot.agents.repo_explorer import RepoExplorerAgent
from src.codepilot.agents.test_agent import TestAgent
from src.codepilot.config import Settings
from src.codepilot.git_helper import GitHelper
from src.codepilot.github_client import GitHubClient
from src.codepilot.guardrails import requires_human_approval
from src.codepilot.memory.semantic_store import SemanticStore
from src.codepilot.memory.session_store import SessionStore
from src.codepilot.models import Issue, TaskContext, TaskState, TaskType
from src.codepilot.skills.catalog import SKILLS

try:
    from langchain_openai import AzureChatOpenAI
    from langchain_core.messages import HumanMessage
    from pydantic import BaseModel
    from pydantic import Field as PydanticField

    class TaskClassification(BaseModel):
        task_type: str = PydanticField(
            description="One of: bug_fix, feature_addition, dependency_update, documentation, config_change"
        )
        reason: str = PydanticField(description="One sentence explaining the classification.")

    HAS_LANGCHAIN = True
except ImportError:
    HAS_LANGCHAIN = False


class Orchestrator:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.github = GitHubClient(
            owner=settings.github_repo_owner,
            repo=settings.github_repo_name,
            token=settings.github_token,
            use_dry_run=settings.use_dry_run,
            max_complexity=settings.max_complexity,
        )
        self.repo_explorer = RepoExplorerAgent(
            token_budget=settings.repo_map_token_budget,
            top_k=settings.repo_map_top_k,
            retrieval_strategy=settings.repo_retrieval_strategy,
        )
        self.coder = CoderAgent(
            azure_endpoint=settings.azure_openai_endpoint,
            azure_api_key=settings.azure_openai_api_key,
            azure_deployment=settings.azure_openai_deployment,
            azure_api_version=settings.azure_openai_api_version,
        )
        self.tester = TestAgent()
        self.pr_agent = PRAgent()
        self.git_helper = GitHelper(settings.repo_root)
        self.session_store = SessionStore()
        self.semantic_store = SemanticStore()
        self.in_progress_issue_ids: set[int] = set()
        self.skipped_issue_ids: set[int] = set()
        self._manual_issue_seq = 0
        self.recent_failed_issue_ids = self._load_recent_failed_issue_ids()

    def _load_recent_failed_issue_ids(self) -> set[int]:
        failed_ids: set[int] = set()
        try:
            for entry in self.session_store.read_last(3):
                state = str(entry.get("state", "")).upper()
                issue_id = entry.get("issue_id")
                if state == "FAILED" and isinstance(issue_id, int) and issue_id > 0:
                    failed_ids.add(issue_id)
        except Exception:
            return set()
        return failed_ids

    def _build_lessons_context(self, issue: Issue, task_type: TaskType) -> tuple[list[dict], str]:
        query = f"{task_type.value} {issue.title} {issue.body}".strip()
        try:
            lessons = self.semantic_store.search(query, top_k=3)
        except Exception:
            return [], ""

        if not lessons:
            return [], ""

        lines: list[str] = []
        for idx, lesson in enumerate(lessons, start=1):
            lesson_issue = lesson.get("issue_id", "?")
            approach = str(lesson.get("approach", "")).strip() or "No approach recorded"
            files = lesson.get("files", [])
            files_preview = ""
            if isinstance(files, list) and files:
                files_preview = ", ".join(str(file_path) for file_path in files[:5])
            if files_preview:
                lines.append(f"{idx}. Issue #{lesson_issue} | {approach} | files: {files_preview}")
            else:
                lines.append(f"{idx}. Issue #{lesson_issue} | {approach}")
        return lessons, "\n".join(lines)

    def _filter_processable_issues(self, issues: list[Issue]) -> list[Issue]:
        filtered: list[Issue] = []
        for issue in issues:
            if issue.issue_id in self.in_progress_issue_ids:
                continue
            if issue.issue_id in self.skipped_issue_ids:
                continue
            if issue.issue_id in self.recent_failed_issue_ids:
                continue
            filtered.append(issue)
        return filtered

    def fetch_processable_issues(self) -> list[Issue]:
        return self._filter_processable_issues(self.github.fetch_open_issues())

    def build_manual_issue(self, task_text: str) -> Issue:
        self._manual_issue_seq += 1
        issue_id = -(1000 + self._manual_issue_seq)
        title = task_text.strip() or "Manual task"
        return Issue(
            issue_id=issue_id,
            title=title,
            body=task_text.strip(),
            labels=["manual"],
            assignee="local-user",
        )

    def skip_issue(self, issue_id: int) -> None:
        self.skipped_issue_ids.add(issue_id)

    def _classify_task_rule_based(self, issue: Issue) -> TaskType:
        text = f"{issue.title} {issue.body}".lower()
        if "bug" in text or "fix" in text or "crash" in text:
            return TaskType.BUG_FIX
        if "upgrade" in text or "dependency" in text:
            return TaskType.DEPENDENCY_UPDATE
        if "docs" in text or "readme" in text:
            return TaskType.DOCUMENTATION
        if "config" in text or "yaml" in text:
            return TaskType.CONFIG_CHANGE
        return TaskType.FEATURE_ADDITION

    def classify_task(self, issue: Issue) -> TaskType:
        if self.settings.has_llm and HAS_LANGCHAIN:
            try:
                llm = AzureChatOpenAI(
                    azure_endpoint=self.settings.azure_openai_endpoint,
                    api_key=self.settings.azure_openai_api_key,
                    azure_deployment=self.settings.azure_openai_deployment,
                    api_version=self.settings.azure_openai_api_version,
                    temperature=0,
                )
                structured = llm.with_structured_output(TaskClassification)
                result = structured.invoke([
                    HumanMessage(content=(
                        f"Classify this GitHub issue into exactly one task type.\n"
                        f"Task types: bug_fix, feature_addition, dependency_update, documentation, config_change\n\n"
                        f"Title: {issue.title}\nDescription: {issue.body}"
                    ))
                ])
                return TaskType(result.task_type)
            except Exception:
                pass
        return self._classify_task_rule_based(issue)

    def build_todos(self, task_type: TaskType) -> list[str]:
        skill = SKILLS[task_type]
        return [f"[{step}]" for step in skill.workflow_steps]

    def run_issue(self, issue: Issue, hitl_approver=None) -> dict:
        """
        Full pipeline for a single issue:
        classify → explore → implement → test → HITL → git commit → PR.

        hitl_approver: optional callable(pr_draft) -> bool. Called when human approval is
        required. Return True to proceed, False to abort. If None, defaults to input() prompt.
        """
        self.in_progress_issue_ids.add(issue.issue_id)
        try:
            task_type = self.classify_task(issue)
            todo_list = self.build_todos(task_type)
            task = TaskContext(
                issue=issue,
                task_type=task_type,
                todos=todo_list,
            )

            task.state = TaskState.EXPLORING
            explore_res = self.repo_explorer.run(self.settings.repo_root, f"{issue.title} {issue.body}")
            task.relevant_files = explore_res.payload.get("relevant_files", [])
            task.decision_log.append(explore_res.message)

            task.state = TaskState.IMPLEMENTING
            lessons_used, lessons_context = self._build_lessons_context(issue, task_type)
            coder_issue_body = issue.body
            if lessons_context:
                coder_issue_body = (
                    f"{issue.body}\n\n"
                    "Similar past lessons (reuse what worked when relevant):\n"
                    f"{lessons_context}"
                )
                task.decision_log.append(f"Loaded {len(lessons_used)} similar lessons from semantic memory")

            code_res = self.coder.run(
                self.settings.repo_root,
                self.settings.sandbox_root,
                task.relevant_files,
                issue_title=issue.title,
                issue_body=coder_issue_body,
            )
            task.current_diff_path = code_res.payload.get("proposed_diff_path", "")
            task.test_output = code_res.payload.get("output", "")
            task.modified_files = code_res.payload.get("modified_files", []) or task.relevant_files[:3]
            task.decision_log.append(code_res.message)

            task.state = TaskState.TESTING
            test_res = self.tester.run(self.settings.sandbox_root)
            task.test_output = (task.test_output + "\n" + test_res.payload.get("output", "")).strip()
            promoted_files: list[str] = []
            if not test_res.ok:
                task.retry_count += 1
                task.decision_log.append("Tests failed")
            else:
                promoted_files = self.coder.promote_changes(
                    self.settings.repo_root,
                    self.settings.sandbox_root,
                    task.modified_files,
                )
                if promoted_files:
                    task.decision_log.append("Promoted sandbox changes to target repo")

            hitl_needed = requires_human_approval(
                target_branch="main",
                file_count=len(task.modified_files),
                action="open pr",
                retry_count=task.retry_count,
            )

            # ── Build PR draft metadata ──────────────────────────────────────────
            pr_res = self.pr_agent.run(task.issue, task.modified_files, task.test_output)
            pr_draft = pr_res.payload.get("pr_draft", {})

            # ── HITL gate ────────────────────────────────────────────────────────
            pr_url = ""
            pr_number = 0
            git_pushed = False
            hitl_approved = False

            if hitl_needed and promoted_files and pr_res.ok:
                if hitl_approver is not None:
                    hitl_approved = hitl_approver(pr_draft)
                else:
                    # Default: block on stdin
                    print("\n" + "─" * 60)
                    print("  HUMAN-IN-THE-LOOP GATE")
                    print("─" * 60)
                    print(f"  Issue  : #{issue.issue_id} — {issue.title}")
                    print(f"  Branch : {pr_draft.get('branch', '')}")
                    print(f"  Files  : {', '.join(promoted_files)}")
                    print("─" * 60)
                    try:
                        answer = input("  Approve push + PR? [y/N] ").strip().lower()
                        hitl_approved = answer in ("y", "yes")
                    except (EOFError, KeyboardInterrupt):
                        hitl_approved = False

                if hitl_approved:
                    task.decision_log.append("HITL approved — pushing branch")

                    # Create git branch + commit + push
                    commit_msg = (
                        f"fix(codepilot): {issue.title}\n\n"
                        f"Automated fix for issue #{issue.issue_id}.\n"
                        f"Generated by CodePilot (fix_source={code_res.payload.get('fix_source', 'unknown')})"
                    )
                    git_ok, git_msg = self.git_helper.create_branch_and_commit(
                        branch=pr_draft["branch"],
                        files=promoted_files,
                        commit_message=commit_msg,
                    )
                    task.decision_log.append(f"Git: {git_msg}")

                    if git_ok:
                        git_pushed = True
                        default_branch = self.git_helper.default_branch()
                        github_pr = self.github.create_pull_request(
                            branch=pr_draft["branch"],
                            base=default_branch,
                            title=pr_draft.get("title", f"CodePilot fix #{issue.issue_id}"),
                            body=pr_draft.get("body", ""),
                            labels=pr_draft.get("labels", []),
                            draft=True,
                        )
                        if github_pr.ok:
                            pr_url = github_pr.pr_url
                            pr_number = github_pr.pr_number
                            task.decision_log.append(f"PR #{pr_number} created: {pr_url}")
                        else:
                            task.decision_log.append(f"PR creation failed: {github_pr.message}")
                else:
                    task.decision_log.append("HITL rejected — branch not pushed")
            elif promoted_files and pr_res.ok:
                # No HITL required — push automatically
                task.decision_log.append("Auto-pushing branch (no HITL required)")
                commit_msg = (
                    f"fix(codepilot): {issue.title}\n\n"
                    f"Automated fix for issue #{issue.issue_id}."
                )
                git_ok, git_msg = self.git_helper.create_branch_and_commit(
                    branch=pr_draft["branch"],
                    files=promoted_files,
                    commit_message=commit_msg,
                )
                task.decision_log.append(f"Git: {git_msg}")

                if git_ok:
                    git_pushed = True
                    default_branch = self.git_helper.default_branch()
                    github_pr = self.github.create_pull_request(
                        branch=pr_draft["branch"],
                        base=default_branch,
                        title=pr_draft.get("title", f"CodePilot fix #{issue.issue_id}"),
                        body=pr_draft.get("body", ""),
                        labels=pr_draft.get("labels", []),
                        draft=True,
                    )
                    if github_pr.ok:
                        pr_url = github_pr.pr_url
                        pr_number = github_pr.pr_number
                        task.decision_log.append(f"PR #{pr_number} created: {pr_url}")
                    else:
                        task.decision_log.append(f"PR creation failed: {github_pr.message}")

            if pr_url:
                task.state = TaskState.PR_OPENED
            elif pr_res.ok:
                task.state = TaskState.DONE
            else:
                task.state = TaskState.FAILED

            if task.state == TaskState.FAILED and issue.issue_id > 0:
                self.recent_failed_issue_ids.add(issue.issue_id)

            if pr_res.ok:
                task.state = TaskState.DONE
                self.recent_failed_issue_ids.discard(issue.issue_id)
                self.semantic_store.add_lesson(
                    {
                        "issue_id": issue.issue_id,
                        "task_type": task.task_type.value,
                        "files": task.modified_files,
                        "approach": "Repo map + guarded sandbox + tests + real git PR",
                    }
                )

            self.session_store.append(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "issue_id": issue.issue_id,
                    "task_type": task.task_type.value,
                    "state": task.state.value,
                    "files_modified": task.modified_files,
                    "retry_count": task.retry_count,
                    "pr_url": pr_url,
                }
            )

            result = {
                "ok": pr_res.ok,
                "issue": issue.issue_id,
                "task_type": task.task_type.value,
                "state": task.state.value,
                "relevant_files": task.relevant_files,
                "promoted_files": promoted_files,
                "diff_preview": task.current_diff_path,
                "fix_source": code_res.payload.get("fix_source", "none"),
                "hitl_required": hitl_needed,
                "hitl_approved": hitl_approved,
                "git_pushed": git_pushed,
                "pr_number": pr_number,
                "pr_url": pr_url,
                "pr_draft": pr_draft,
                "lessons_used": len(lessons_used),
                "log": task.decision_log,
            }
            return result
        finally:
            self.in_progress_issue_ids.discard(issue.issue_id)

    def run_once(self, hitl_approver=None) -> dict:
        issues = self.fetch_processable_issues()
        if not issues:
            return {"ok": True, "message": "No issues found"}
        return self.run_issue(issues[0], hitl_approver=hitl_approver)

    def run_polling_loop(
        self,
        hitl_approver=None,
        stop_requested: Callable[[], bool] | None = None,
        on_issues_fetched: Callable[[list[Issue]], None] | None = None,
        on_result: Callable[[dict], None] | None = None,
    ) -> None:
        import time

        interval = max(1, int(self.settings.poll_interval_seconds))
        should_stop = stop_requested or (lambda: False)

        while not should_stop():
            issues = self.fetch_processable_issues()
            if on_issues_fetched is not None:
                on_issues_fetched(issues)

            if issues:
                result = self.run_issue(issues[0], hitl_approver=hitl_approver)
                if on_result is not None:
                    on_result(result)
                continue

            slept = 0
            while slept < interval and not should_stop():
                time.sleep(1)
                slept += 1
