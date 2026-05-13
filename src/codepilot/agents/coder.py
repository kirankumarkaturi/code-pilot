from __future__ import annotations

import difflib
import subprocess
from pathlib import Path
import shutil
from typing import Optional

from src.codepilot.guardrails import ensure_dir, is_command_allowed, is_file_edit_allowed
from src.codepilot.models import AgentResult

try:
    from langchain_openai import AzureChatOpenAI
    from langchain_core.messages import HumanMessage, SystemMessage
    from pydantic import BaseModel
    from pydantic import Field as PydanticField

    class FileEdit(BaseModel):
        file_path: str = PydanticField(description="Relative file path from repo root, e.g. app/calculator.py")
        new_content: str = PydanticField(description="Complete new content of the file after the fix. Must be the full file, not a snippet.")
        explanation: str = PydanticField(description="One sentence explaining what was changed and why.")

    class LLMCoderOutput(BaseModel):
        edits: list[FileEdit] = PydanticField(description="List of file edits to apply. Only include files that actually need changing.")
        summary: str = PydanticField(description="One-line summary of all changes made.")

    HAS_LANGCHAIN = True
except ImportError:
    HAS_LANGCHAIN = False


class CoderAgent:
    def __init__(
        self,
        azure_endpoint: str = "",
        azure_api_key: str = "",
        azure_deployment: str = "gpt-4o",
        azure_api_version: str = "2024-12-01-preview",
    ) -> None:
        self.azure_endpoint = azure_endpoint
        self.azure_api_key = azure_api_key
        self.azure_deployment = azure_deployment
        self.azure_api_version = azure_api_version
        self._llm: Optional[object] = None

    @property
    def llm(self) -> Optional[object]:
        if self._llm is None and self.azure_endpoint and self.azure_api_key and HAS_LANGCHAIN:
            self._llm = AzureChatOpenAI(
                azure_endpoint=self.azure_endpoint,
                api_key=self.azure_api_key,
                azure_deployment=self.azure_deployment,
                api_version=self.azure_api_version,
                temperature=0,
            )
        return self._llm

    def _read_sandbox_files(self, sandbox: Path) -> dict[str, str]:
        contents: dict[str, str] = {}
        for path in sandbox.rglob("*.py"):
            if "__pycache__" in str(path) or "working" in str(path):
                continue
            rel = str(path.relative_to(sandbox)).replace("\\", "/")
            try:
                contents[rel] = path.read_text(encoding="utf-8")
            except Exception:
                pass
        return contents

    def _generate_fix_with_llm(
        self,
        issue_title: str,
        issue_body: str,
        sandbox: Path,
    ) -> tuple[list[str], str]:
        if not self.llm or not HAS_LANGCHAIN:
            return [], ""

        file_contents = self._read_sandbox_files(sandbox)
        if not file_contents:
            return [], ""

        source_block = "\n\n".join(
            f"### {path}\n```python\n{content}\n```"
            for path, content in file_contents.items()
            if not path.startswith("tests/")
        )
        tests_block = "\n\n".join(
            f"### {path}\n```python\n{content}\n```"
            for path, content in file_contents.items()
            if path.startswith("tests/")
        )

        structured_llm = self.llm.with_structured_output(LLMCoderOutput)
        messages = [
            SystemMessage(content=(
                "You are a precise software engineer fixing GitHub issues. "
                "Return only the minimal correct changes needed. "
                "Return the COMPLETE new file content for every file you edit — not just the changed lines. "
                "Only edit source files, not test files."
            )),
            HumanMessage(content=(
                f"## GitHub Issue\n"
                f"**Title:** {issue_title}\n\n"
                f"**Description:**\n{issue_body}\n\n"
                f"## Source Files (may need editing)\n{source_block}\n\n"
                f"## Test Files (read-only — shows expected behavior)\n{tests_block}"
            )),
        ]

        try:
            result: LLMCoderOutput = structured_llm.invoke(messages)
        except Exception as exc:
            return [], f"LLM call failed: {exc}"

        modified_files: list[str] = []
        diff_chunks: list[str] = []

        for edit in result.edits:
            target = sandbox / edit.file_path
            if not target.exists():
                continue
            original = target.read_text(encoding="utf-8")
            if original == edit.new_content:
                continue
            target.write_text(edit.new_content, encoding="utf-8")
            modified_files.append(edit.file_path)
            diff_chunks.extend(
                difflib.unified_diff(
                    original.splitlines(),
                    edit.new_content.splitlines(),
                    fromfile=f"a/{edit.file_path}",
                    tofile=f"b/{edit.file_path}",
                    lineterm="",
                )
            )

        return modified_files, "\n".join(diff_chunks)

    def _apply_rule_based_fix(self, sandbox: Path) -> tuple[list[str], str]:
        modified_files: list[str] = []
        diff_chunks: list[str] = []

        target = sandbox / "app" / "calculator.py"
        tests_file = sandbox / "tests" / "test_calculator.py"
        if not target.exists() or not tests_file.exists():
            return modified_files, ""

        original = target.read_text(encoding="utf-8")
        tests_text = tests_file.read_text(encoding="utf-8")

        updated = original
        if "safe_divide" in tests_text and "is None" in tests_text:
            updated = updated.replace("return 0", "return None")

        if updated != original:
            target.write_text(updated, encoding="utf-8")
            modified_files.append("app/calculator.py")
            diff_chunks.extend(
                difflib.unified_diff(
                    original.splitlines(),
                    updated.splitlines(),
                    fromfile="a/app/calculator.py",
                    tofile="b/app/calculator.py",
                    lineterm="",
                )
            )

        return modified_files, "\n".join(diff_chunks)

    def _copy_supporting_source_dirs(self, repo: Path, sandbox: Path) -> None:
        for child in repo.iterdir():
            if not child.is_dir():
                continue
            if child.name.startswith(".") or child.name in {"tests", "sandbox", "__pycache__"}:
                continue

            has_python_files = any(path.suffix == ".py" for path in child.rglob("*.py"))
            if not has_python_files:
                continue

            shutil.copytree(child, sandbox / child.name, dirs_exist_ok=True)

    def _copy_into_sandbox(self, repo_root: str, sandbox_root: str, relevant_files: list[str]) -> None:
        repo = Path(repo_root)
        sandbox = Path(sandbox_root)

        # Reset sandbox content for deterministic runs.
        if sandbox.exists():
            for child in sandbox.iterdir():
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
        sandbox.mkdir(parents=True, exist_ok=True)

        for rel in relevant_files:
            src = repo / rel
            if not src.exists() or not src.is_file():
                continue
            dst = sandbox / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

        self._copy_supporting_source_dirs(repo, sandbox)

        tests_dir = repo / "tests"
        if tests_dir.exists() and tests_dir.is_dir():
            shutil.copytree(tests_dir, sandbox / "tests", dirs_exist_ok=True)

    def run(
        self,
        repo_root: str,
        sandbox_root: str,
        relevant_files: list[str],
        issue_title: str = "",
        issue_body: str = "",
        command: str = "python -m pytest -q",
    ) -> AgentResult:
        ensure_dir(sandbox_root)
        self._copy_into_sandbox(repo_root, sandbox_root, relevant_files)

        allowed, reason = is_command_allowed(command, sandbox_root)
        if not allowed:
            return AgentResult(ok=False, message=f"Command blocked: {reason}")

        working_dir = Path(sandbox_root) / "working"
        working_dir.mkdir(parents=True, exist_ok=True)
        working_dir.joinpath("coder_marker.txt").write_text("Coder executed in sandbox.\n", encoding="utf-8")

        fix_source = "none"

        # Primary path: LLM-generated fix.
        modified_files, diff_text = self._generate_fix_with_llm(
            issue_title, issue_body, Path(sandbox_root)
        )
        if modified_files:
            fix_source = "llm"
        else:
            # Fallback: rule-based fix for known patterns.
            modified_files, diff_text = self._apply_rule_based_fix(Path(sandbox_root))
            if modified_files:
                fix_source = "rule_based"

        proposed_diff = Path(sandbox_root) / "working" / "proposed_diff.txt"
        proposed_diff.write_text(diff_text or "No code changes generated.\n", encoding="utf-8")

        try:
            result = subprocess.run(
                command,
                cwd=sandbox_root,
                shell=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
            output = (result.stdout or "") + "\n" + (result.stderr or "")
        except Exception as exc:  # pragma: no cover
            output = f"Execution failed: {exc}"

        blocked_edits: list[str] = []
        for file_path in relevant_files:
            ok, msg = is_file_edit_allowed(file_path)
            if not ok:
                blocked_edits.append(f"{file_path}: {msg}")

        return AgentResult(
            ok=not blocked_edits,
            message=f"Coder completed ({fix_source})",
            payload={
                "output": output.strip(),
                "proposed_diff_path": str(proposed_diff),
                "modified_files": modified_files,
                "fix_source": fix_source,
                "blocked_edits": blocked_edits,
            },
        )

    def promote_changes(self, repo_root: str, sandbox_root: str, modified_files: list[str]) -> list[str]:
        repo = Path(repo_root)
        sandbox = Path(sandbox_root)
        promoted: list[str] = []

        for rel in modified_files:
            src = sandbox / rel
            dst = repo / rel
            if not src.exists() or not src.is_file():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            promoted.append(rel)

        return promoted
