from __future__ import annotations

from pathlib import Path


BLOCKED_COMMAND_SNIPPETS = ["rm -rf", "curl", "wget", "pip install", "git push"]
BLOCKED_EDIT_PATTERNS = [".env", ".pem", ".key", "credentials", ".secret"]


def is_command_allowed(command: str, sandbox_root: str) -> tuple[bool, str]:
    lowered = command.lower()
    for snippet in BLOCKED_COMMAND_SNIPPETS:
        if snippet in lowered:
            return False, f"Blocked command snippet detected: {snippet}"
    if ".." in command and sandbox_root not in command:
        return False, "Command may target paths outside sandbox"
    return True, "allowed"


def is_file_edit_allowed(file_path: str) -> tuple[bool, str]:
    lowered = file_path.lower()
    for pattern in BLOCKED_EDIT_PATTERNS:
        if pattern in lowered:
            return False, f"Blocked file pattern detected: {pattern}"
    return True, "allowed"


def requires_human_approval(target_branch: str, file_count: int, action: str, retry_count: int) -> bool:
    if target_branch in {"main", "master"}:
        return True
    if file_count > 5:
        return True
    if "git push" in action.lower():
        return True
    if retry_count >= 2:
        return True
    return False


def ensure_dir(path: str) -> None:
    Path(path).mkdir(parents=True, exist_ok=True)
