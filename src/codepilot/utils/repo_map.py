from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Iterable


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


def _estimate_tokens(text: str) -> int:
    # Lightweight approximation keeps token accounting cheap and deterministic.
    return max(1, len(text.split()))


def _run_git(repo_root: Path, args: list[str]) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return ""
    if result.returncode != 0:
        return ""
    return (result.stdout or "").strip()


def _git_signature(repo_root: Path) -> str:
    head = _run_git(repo_root, ["rev-parse", "HEAD"])
    # Includes tracked + untracked changes for cache invalidation.
    status = _run_git(repo_root, ["status", "--porcelain"])
    return f"{head}\n{status}".strip()


def _cache_path(repo_root: Path) -> Path:
    cache_dir = repo_root / ".codepilot"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / "repo_map_cache.json"


def _load_cached_map(repo_root: Path, signature: str, token_budget: int) -> list[dict[str, str]] | None:
    path = _cache_path(repo_root)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

    if payload.get("signature") != signature:
        return None
    if int(payload.get("token_budget", 0)) != token_budget:
        return None

    items = payload.get("items")
    if not isinstance(items, list):
        return None
    return items


def _write_cached_map(repo_root: Path, signature: str, token_budget: int, items: list[dict[str, str]]) -> None:
    payload = {
        "signature": signature,
        "token_budget": token_budget,
        "items": items,
    }
    _cache_path(repo_root).write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _chunk_text(text: str, chunk_size: int = 1200, overlap: int = 200) -> list[str]:
    content = (text or "").strip()
    if not content:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(content):
        end = min(len(content), start + chunk_size)
        chunk = content[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(content):
            break
        start = max(0, end - overlap)
    return chunks


def _embedding_signature_path(repo_root: Path) -> Path:
    cache_dir = repo_root / ".codepilot"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / "embedding_signature.txt"


def _retrieve_embedding_files(
    task_text: str,
    repo_map: list[dict[str, str]],
    repo_root: str,
    k: int,
    signature: str,
    embedding_model_name: str,
) -> tuple[list[str], str]:
    root = Path(repo_root)

    model_name = (embedding_model_name or "all-MiniLM-L6-v2").strip()
    if model_name != "all-MiniLM-L6-v2":
        return [], f"embedding_unavailable:model_not_supported:{model_name}"

    try:
        import chromadb
    except Exception:
        return [], "embedding_unavailable:chromadb_import"

    db_path = root / ".codepilot" / "chroma"
    db_path.mkdir(parents=True, exist_ok=True)
    collection_name = "repo_chunks_all_minilm_l6_v2"

    try:
        client = chromadb.PersistentClient(path=str(db_path))
    except Exception:
        return [], "embedding_unavailable:chroma_client"

    signature_path = _embedding_signature_path(root)
    cached_signature = ""
    if signature_path.exists():
        try:
            cached_signature = signature_path.read_text(encoding="utf-8")
        except Exception:
            cached_signature = ""

    try:
        if cached_signature != signature:
            try:
                client.delete_collection(collection_name)
            except Exception:
                pass

        collection = client.get_or_create_collection(name=collection_name)

        if cached_signature != signature:
            docs: list[str] = []
            ids: list[str] = []
            metadatas: list[dict[str, str]] = []

            doc_id = 0
            for item in repo_map:
                rel = item.get("path", "")
                if not rel:
                    continue
                path = root / rel
                try:
                    text = path.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue

                chunks = _chunk_text(text)
                for idx, chunk in enumerate(chunks):
                    docs.append(chunk)
                    ids.append(f"doc-{doc_id}-{idx}")
                    metadatas.append({"path": rel})
                doc_id += 1

            if docs:
                collection.add(documents=docs, ids=ids, metadatas=metadatas)
            signature_path.write_text(signature, encoding="utf-8")

        if collection.count() == 0:
            return [], "embedding_empty:index"

        result = collection.query(query_texts=[task_text], n_results=max(k * 3, 10))
    except Exception:
        return [], "embedding_error:query"

    metadatas = result.get("metadatas") if isinstance(result, dict) else None
    if not metadatas:
        return [], "embedding_empty:results"

    ranked_paths: list[str] = []
    seen: set[str] = set()
    first_batch = metadatas[0] if metadatas else []
    for meta in first_batch:
        if not isinstance(meta, dict):
            continue
        path = str(meta.get("path", ""))
        if not path or path in seen:
            continue
        seen.add(path)
        ranked_paths.append(path)
        if len(ranked_paths) >= k:
            break
    if not ranked_paths:
        return [], "embedding_empty:paths"
    return ranked_paths, f"embedding:{model_name}"


def _iter_exported_symbols(lines: Iterable[str], suffix: str) -> list[str]:
    symbols: list[str] = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        if suffix == ".py":
            if line.startswith("def ") or line.startswith("class "):
                name = line.split("(", 1)[0].replace("def ", "").replace("class ", "").replace(":", "").strip()
                if name:
                    symbols.append(name)
        elif suffix in {".ts", ".js"}:
            if line.startswith("export ") or line.startswith("function ") or line.startswith("class "):
                symbols.append(line.split("{", 1)[0].strip())
        if len(symbols) >= 4:
            break
    return symbols


def _first_description_line(lines: Iterable[str]) -> str:
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line.startswith(("#", "//", "\"\"\"", "'''")):
            return line[:120]
        if line.startswith(("import ", "from ")):
            continue
        return line[:120]
    return ""


def _summarize_file(path: Path, rel: str) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        lines = []

    symbols = _iter_exported_symbols(lines, path.suffix)
    desc = _first_description_line(lines)
    symbol_text = ", ".join(symbols) if symbols else "none"
    summary = f"File {rel}; symbols: {symbol_text}; desc: {desc or 'n/a'}"
    return {
        "path": rel,
        "summary": summary,
        "language": path.suffix.lstrip("."),
    }


def build_repo_map(repo_root: str, token_budget: int = 4000) -> list[dict[str, str]]:
    root = Path(repo_root)
    signature = _git_signature(root)
    cached = _load_cached_map(root, signature, token_budget)
    if cached is not None:
        return cached

    items: list[dict[str, str]] = []
    used = 0

    for path in root.rglob("*"):
        if not path.is_file() or path.suffix not in SUPPORTED_SUFFIXES:
            continue
        if any(part in IGNORED_DIR_NAMES for part in path.parts):
            continue
        rel = str(path.relative_to(root)).replace("\\", "/")
        item = _summarize_file(path, rel)
        cost = _estimate_tokens(item["summary"])
        if used + cost > token_budget:
            break
        items.append(item)
        used += cost

    _write_cached_map(root, signature, token_budget, items)
    return items


def _retrieve_keyword_files(task_text: str, repo_map: list[dict[str, str]], k: int = 10) -> list[str]:
    keywords = {w.lower() for w in task_text.split() if len(w) > 2}
    scored: list[tuple[int, str]] = []
    for item in repo_map:
        text = f"{item['path']} {item['summary']}".lower()
        score = sum(1 for kw in keywords if kw in text)
        scored.append((score, item["path"]))
    scored.sort(reverse=True)
    return [p for s, p in scored if s > 0][:k]


def retrieve_relevant_files(
    task_text: str,
    repo_map: list[dict[str, str]],
    k: int = 10,
    strategy: str = "keyword",
    repo_root: str = ".",
    embedding_model_name: str = "all-MiniLM-L6-v2",
) -> tuple[list[str], str]:
    chosen = (strategy or "keyword").strip().lower()
    if chosen == "embedding":
        signature = _git_signature(Path(repo_root))
        embedded, mode = _retrieve_embedding_files(
            task_text,
            repo_map,
            repo_root,
            k,
            signature,
            embedding_model_name,
        )
        if embedded:
            return embedded, mode
        keyword_files = _retrieve_keyword_files(task_text, repo_map, k)
        if keyword_files:
            return keyword_files, f"{mode}->keyword"
        return [], f"{mode}->keyword_empty"

    keyword_files = _retrieve_keyword_files(task_text, repo_map, k)
    if keyword_files:
        return keyword_files, "keyword"
    return [], "keyword_empty"
