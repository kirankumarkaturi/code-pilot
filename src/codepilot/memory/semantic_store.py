from __future__ import annotations

import json
from pathlib import Path


class SemanticStore:
    def __init__(self, path: str = ".codepilot/lessons.json") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("[]", encoding="utf-8")

    def add_lesson(self, lesson: dict) -> None:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        data.append(lesson)
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def search(self, query: str, top_k: int = 3) -> list[dict]:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        query_terms = {q.lower() for q in query.split() if len(q) > 2}
        scored = []
        for item in data:
            blob = json.dumps(item).lower()
            score = sum(1 for term in query_terms if term in blob)
            scored.append((score, item))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for score, item in scored if score > 0][:top_k]
