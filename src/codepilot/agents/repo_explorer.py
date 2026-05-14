from __future__ import annotations

from src.codepilot.models import AgentResult
from src.codepilot.utils.repo_map import build_repo_map, retrieve_relevant_files


class RepoExplorerAgent:
    def __init__(
        self,
        token_budget: int = 4000,
        top_k: int = 10,
        retrieval_strategy: str = "keyword",
        embedding_model_name: str = "all-MiniLM-L6-v2",
    ) -> None:
        self.token_budget = token_budget
        self.top_k = top_k
        self.retrieval_strategy = retrieval_strategy
        self.embedding_model_name = embedding_model_name

    def run(self, repo_root: str, task_text: str) -> AgentResult:
        repo_map = build_repo_map(repo_root, token_budget=self.token_budget)
        relevant, retrieval_mode = retrieve_relevant_files(
            task_text,
            repo_map,
            k=self.top_k,
            strategy=self.retrieval_strategy,
            repo_root=repo_root,
            embedding_model_name=self.embedding_model_name,
        )
        if not relevant:
            # Fallback to top files for empty or tiny repositories.
            relevant = [item["path"] for item in repo_map[: self.top_k]]
            retrieval_mode = f"{retrieval_mode}+topk_fallback"
        return AgentResult(
            ok=True,
            message=f"Repo map built (retrieval={retrieval_mode})",
            payload={
                "repo_map": repo_map,
                "relevant_files": relevant,
                "retrieval_mode": retrieval_mode,
            },
        )
