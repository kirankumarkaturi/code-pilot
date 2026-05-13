from __future__ import annotations

import json

from src.codepilot.config import settings
from src.codepilot.orchestrator import Orchestrator


def main() -> None:
    orchestrator = Orchestrator(settings)
    result = orchestrator.run_once()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
