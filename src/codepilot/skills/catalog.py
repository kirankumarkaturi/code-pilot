from __future__ import annotations

from dataclasses import dataclass

from src.codepilot.models import TaskType


@dataclass
class Skill:
    name: str
    instructions: str
    workflow_steps: list[str]
    example_prompts: list[str]
    forbidden_actions: list[str]


SKILLS: dict[TaskType, Skill] = {
    TaskType.BUG_FIX: Skill(
        name="bug_fix_skill",
        instructions="Reproduce the bug first with a failing test, then make it pass.",
        workflow_steps=["reproduce", "localize", "fix", "verify"],
        example_prompts=["Fix null pointer in parser", "Resolve broken login redirect"],
        forbidden_actions=["skip tests", "edit secrets"],
    ),
    TaskType.FEATURE_ADDITION: Skill(
        name="feature_addition_skill",
        instructions="Match existing patterns before introducing new code paths.",
        workflow_steps=["explore_pattern", "design", "implement", "test", "document"],
        example_prompts=["Add export CSV endpoint", "Add settings toggle"],
        forbidden_actions=["breaking public API"],
    ),
    TaskType.DEPENDENCY_UPDATE: Skill(
        name="dependency_update_skill",
        instructions="Review changelog and validate lockfile updates with full tests.",
        workflow_steps=["check_changelog", "update", "resolve_conflicts", "test_all"],
        example_prompts=["Upgrade requests 2.x to 3.x"],
        forbidden_actions=["network installs in restricted mode"],
    ),
    TaskType.DOCUMENTATION: Skill(
        name="documentation_skill",
        instructions="Keep style consistent and include accurate examples.",
        workflow_steps=["read_existing", "draft", "review_accuracy", "update_index"],
        example_prompts=["Update API guide for auth changes"],
        forbidden_actions=["inventing behavior not in code"],
    ),
    TaskType.CONFIG_CHANGE: Skill(
        name="config_change_skill",
        instructions="Use minimal, reversible config changes and validate startup.",
        workflow_steps=["review_current", "propose_delta", "apply", "verify"],
        example_prompts=["Adjust timeout settings"],
        forbidden_actions=["editing credential files"],
    ),
}
