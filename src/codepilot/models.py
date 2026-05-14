from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TaskType(str, Enum):
    BUG_FIX = "bug_fix"
    FEATURE_ADDITION = "feature_addition"
    DEPENDENCY_UPDATE = "dependency_update"
    DOCUMENTATION = "documentation"
    CONFIG_CHANGE = "config_change"


class TaskState(str, Enum):
    TRIAGED = "TRIAGED"
    EXPLORING = "EXPLORING"
    IMPLEMENTING = "IMPLEMENTING"
    TESTING = "TESTING"
    PR_OPENED = "PR_OPENED"
    DONE = "DONE"
    FAILED = "FAILED"


@dataclass
class Issue:
    issue_id: int
    title: str
    body: str
    labels: list[str] = field(default_factory=list)
    assignee: str | None = None
    reporter: str | None = None


@dataclass
class TaskContext:
    issue: Issue
    task_type: TaskType
    state: TaskState = TaskState.TRIAGED
    relevant_files: list[str] = field(default_factory=list)
    todos: list[str] = field(default_factory=list)
    modified_files: list[str] = field(default_factory=list)
    test_output: str = ""
    retry_count: int = 0
    current_diff_path: str = ""
    decision_log: list[str] = field(default_factory=list)


@dataclass
class AgentResult:
    ok: bool
    message: str
    payload: dict[str, Any] = field(default_factory=dict)
