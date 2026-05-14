from __future__ import annotations

import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    def load_dotenv() -> None:
        return None


load_dotenv()


@dataclass
class Settings:
    github_repo_owner: str = os.getenv("GITHUB_REPO_OWNER", "")
    github_repo_name: str = os.getenv("GITHUB_REPO_NAME", "")
    github_token: str = os.getenv("GITHUB_TOKEN", "")
    azure_openai_endpoint: str = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    azure_openai_api_key: str = os.getenv("AZURE_OPENAI_API_KEY", "")
    azure_openai_deployment: str = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
    azure_openai_api_version: str = os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")
    poll_interval_seconds: int = int(os.getenv("POLL_INTERVAL_SECONDS", "300"))
    repo_root: str = os.getenv("REPO_ROOT", ".")
    sandbox_root: str = os.getenv("SANDBOX_ROOT", "./sandbox")
    max_complexity: int = int(os.getenv("MAX_COMPLEXITY", "6"))
    repo_map_token_budget: int = int(os.getenv("REPO_MAP_TOKEN_BUDGET", "4000"))
    repo_map_top_k: int = int(os.getenv("REPO_MAP_TOP_K", "10"))
    use_dry_run: bool = os.getenv("USE_DRY_RUN", "true").lower() == "true"

    @property
    def has_llm(self) -> bool:
        return bool(self.azure_openai_endpoint and self.azure_openai_api_key)


settings = Settings()
