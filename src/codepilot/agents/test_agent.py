from __future__ import annotations

import subprocess

from src.codepilot.models import AgentResult


class TestAgent:
    def run(self, sandbox_root: str, test_command: str = "python -m pytest -q") -> AgentResult:
        try:
            result = subprocess.run(
                test_command,
                cwd=sandbox_root,
                shell=True,
                capture_output=True,
                text=True,
                timeout=120,
            )
            output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
            ok = result.returncode == 0
            return AgentResult(ok=ok, message="Tests executed", payload={"output": output})
        except Exception as exc:  # pragma: no cover
            return AgentResult(ok=False, message=f"Test execution error: {exc}", payload={"output": ""})
