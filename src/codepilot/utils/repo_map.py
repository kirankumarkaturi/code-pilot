from __future__ import annotations

from pathlib import Path


SUPPORTED_SUFFIXES = {".py", ".md", ".txt", ".json", ".yml", ".yaml", ".toml", ".ts", ".js"}
IGNORED_DIR_NAMES = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    "node_modules",
    "sandbox",
    ".codepilot",
}


def build_repo_map(repo_root: str, token_budget: int = 4000) -> list[dict[str, str]]:
    root = Path(repo_root)
    items: list[dict[str, str]] = []
    used = 0

    for path in root.rglob("*"):
        if not path.is_file() or path.suffix not in SUPPORTED_SUFFIXES:
            continue
        if any(part in IGNORED_DIR_NAMES for part in path.parts):
            continue
        rel = str(path.relative_to(root)).replace("\\", "/")
        summary = f"File {rel}"
        cost = len(summary.split())
        if used + cost > token_budget:
            break
        items.append({"path": rel, "summary": summary, "language": path.suffix.lstrip(".")})
        used += cost
    return items


def retrieve_relevant_files(task_text: str, repo_map: list[dict[str, str]], k: int = 10) -> list[str]:
    keywords = {w.lower() for w in task_text.split() if len(w) > 2}
    scored: list[tuple[int, str]] = []
    for item in repo_map:
        text = f"{item['path']} {item['summary']}".lower()
        score = sum(1 for kw in keywords if kw in text)
        scored.append((score, item["path"]))
    scored.sort(reverse=True)
    return [p for s, p in scored if s > 0][:k]
