from __future__ import annotations

from src.codepilot.models import AgentResult
from src.codepilot.utils.repo_map import build_repo_map, retrieve_relevant_files


class RepoExplorerAgent:
    def __init__(self, token_budget: int = 4000, top_k: int = 10, retrieval_strategy: str = "keyword") -> None:
        self.token_budget = token_budget
        self.top_k = top_k
        self.retrieval_strategy = retrieval_strategy

    def run(self, repo_root: str, task_text: str) -> AgentResult:
        repo_map = build_repo_map(repo_root, token_budget=self.token_budget)
        relevant = retrieve_relevant_files(
            task_text,
            repo_map,
            k=self.top_k,
            strategy=self.retrieval_strategy,
            repo_root=repo_root,
        )
        if not relevant:
            # Fallback to top files for empty or tiny repositories.
            relevant = [item["path"] for item in repo_map[: self.top_k]]
        return AgentResult(
            ok=True,
            message="Repo map built",
            payload={"repo_map": repo_map, "relevant_files": relevant},
        )
