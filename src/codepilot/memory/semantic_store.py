from __future__ import annotations

import json
from pathlib import Path


class SemanticStore:
    def __init__(self, repo_root: str = ".", embedding_model_name: str = "all-MiniLM-L6-v2") -> None:
        self.repo_root = Path(repo_root)
        self.embedding_model_name = (embedding_model_name or "all-MiniLM-L6-v2").strip()
        self.base_dir = self.repo_root / ".codepilot"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.base_dir / "lessons.json"
        if not self.path.exists():
            self.path.write_text("[]", encoding="utf-8")
        self.chroma_path = self.base_dir / "semantic_chroma"

    @property
    def _collection_name(self) -> str:
        if self.embedding_model_name == "all-MiniLM-L6-v2":
            return "lessons_all_minilm_l6_v2"
        return f"lessons_{self.embedding_model_name.lower().replace('-', '_')}"

    def _get_collection(self):
        if self.embedding_model_name != "all-MiniLM-L6-v2":
            return None
        try:
            import chromadb
        except Exception:
            return None

        self.chroma_path.mkdir(parents=True, exist_ok=True)
        try:
            client = chromadb.PersistentClient(path=str(self.chroma_path))
            return client.get_or_create_collection(name=self._collection_name)
        except Exception:
            return None

    def _lesson_document(self, lesson: dict) -> str:
        issue_id = lesson.get("issue_id", "?")
        task_type = lesson.get("task_type", "unknown")
        approach = lesson.get("approach", "")
        files = lesson.get("files", [])
        file_text = ", ".join(str(file_path) for file_path in files) if isinstance(files, list) else ""
        return (
            f"Repository: {self.repo_root.name}\n"
            f"Issue: {issue_id}\n"
            f"Task type: {task_type}\n"
            f"Approach: {approach}\n"
            f"Files: {file_text}"
        )

    def _keyword_search(self, query: str, top_k: int) -> list[dict]:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        query_terms = {q.lower() for q in query.split() if len(q) > 2}
        scored = []
        for item in data:
            blob = json.dumps(item).lower()
            score = sum(1 for term in query_terms if term in blob)
            scored.append((score, item))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for score, item in scored if score > 0][:top_k]

    def add_lesson(self, lesson: dict) -> None:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        data.append(lesson)
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

        collection = self._get_collection()
        if collection is None:
            return

        issue_id = lesson.get("issue_id", "unknown")
        doc_id = f"lesson-{self.repo_root.name}-{issue_id}"
        try:
            try:
                collection.delete(ids=[doc_id])
            except Exception:
                pass
            collection.add(
                documents=[self._lesson_document(lesson)],
                ids=[doc_id],
                metadatas=[
                    {
                        "issue_id": str(lesson.get("issue_id", "")),
                        "task_type": str(lesson.get("task_type", "")),
                        "repo": self.repo_root.name,
                        "payload": json.dumps(lesson),
                    }
                ],
            )
        except Exception:
            return

    def search(self, query: str, top_k: int = 3) -> list[dict]:
        collection = self._get_collection()
        if collection is None:
            return self._keyword_search(query, top_k)

        try:
            result = collection.query(query_texts=[query], n_results=top_k)
        except Exception:
            return self._keyword_search(query, top_k)

        metadatas = result.get("metadatas") if isinstance(result, dict) else None
        if not metadatas:
            return self._keyword_search(query, top_k)

        lessons: list[dict] = []
        for meta in metadatas[0]:
            if not isinstance(meta, dict):
                continue
            payload = meta.get("payload", "")
            if not payload:
                continue
            try:
                lessons.append(json.loads(payload))
            except json.JSONDecodeError:
                continue

        if lessons:
            return lessons[:top_k]
        return self._keyword_search(query, top_k)
