from __future__ import annotations

from src.codepilot.models import AgentResult
from src.codepilot.utils.repo_map import build_repo_map, retrieve_relevant_files


class RepoExplorerAgent:
    def run(self, repo_root: str, task_text: str) -> AgentResult:
        repo_map = build_repo_map(repo_root)
        relevant = retrieve_relevant_files(task_text, repo_map)
        if not relevant:
            # Fallback to top files for empty or tiny repositories.
            relevant = [item["path"] for item in repo_map[:10]]
        return AgentResult(
            ok=True,
            message="Repo map built",
            payload={"repo_map": repo_map, "relevant_files": relevant},
        )
