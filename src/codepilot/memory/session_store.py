from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class SessionStore:
    def __init__(self, path: str = ".codepilot/session_log.json") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("[]", encoding="utf-8")

    def append(self, event: dict[str, Any]) -> None:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        data.append(event)
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def read_last(self, n: int = 3) -> list[dict[str, Any]]:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        return data[-n:]
