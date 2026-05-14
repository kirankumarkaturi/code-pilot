"""Git operations on the target repo using subprocess."""
from __future__ import annotations

import subprocess
from pathlib import Path


class GitHelper:
    def __init__(self, repo_root: str) -> None:
        self.root = Path(repo_root).resolve()

    def _run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=check,
        )

    def current_branch(self) -> str:
        result = self._run("rev-parse", "--abbrev-ref", "HEAD")
        return result.stdout.strip()

    def default_branch(self) -> str:
        """Return the default remote branch (main or master)."""
        result = self._run("remote", "show", "origin", check=False)
        for line in result.stdout.splitlines():
            if "HEAD branch:" in line:
                return line.split(":", 1)[1].strip()
        return "main"

    def branch_exists_remote(self, branch: str) -> bool:
        result = self._run("ls-remote", "--heads", "origin", branch, check=False)
        return bool(result.stdout.strip())

    def create_branch_and_commit(
        self,
        branch: str,
        files: list[str],
        commit_message: str,
    ) -> tuple[bool, str]:
        """
        Creates `branch` off the default branch, stages `files`, commits, and pushes.
        Returns (success, message).
        """
        try:
            # Fetch latest
            self._run("fetch", "origin", check=False)

            # Keep local promoted changes intact and create/reset branch from current HEAD.
            # Using origin/<default> here can fail when working tree contains local edits.
            checkout = self._run("checkout", "-B", branch, check=False)
            if checkout.returncode != 0:
                return False, f"Checkout failed: {checkout.stderr.strip()}"

            # Stage only the promoted files
            for f in files:
                rel = f.replace("\\", "/")
                self._run("add", rel)

            # Check if there's anything to commit
            status = self._run("status", "--porcelain")
            if not status.stdout.strip():
                return False, "Nothing to commit — files already match repo state"

            self._run("commit", "-m", commit_message)

            # Push
            push = self._run("push", "-u", "origin", branch, "--force", check=False)
            if push.returncode != 0:
                return False, f"Push failed: {push.stderr.strip()}"

            return True, f"Branch '{branch}' pushed to origin"

        except subprocess.CalledProcessError as exc:
            return False, f"Git error: {exc.stderr.strip()}"
