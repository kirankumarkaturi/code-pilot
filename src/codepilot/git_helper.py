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
                details = (checkout.stderr or checkout.stdout or "unknown error").strip()
                return False, f"Checkout failed: {details}"

            # Stage only the promoted files
            for f in files:
                rel = f.replace("\\", "/")
                self._run("add", rel)

            # Check only staged changes to avoid false positives from untracked files.
            staged = self._run("diff", "--cached", "--name-only", check=False)
            if staged.returncode != 0:
                details = (staged.stderr or staged.stdout or "unknown error").strip()
                return False, f"Unable to inspect staged files: {details}"
            if not staged.stdout.strip():
                return False, "Nothing staged to commit for promoted files"

            commit = self._run("commit", "-m", commit_message, check=False)
            if commit.returncode != 0:
                details = (commit.stderr or commit.stdout or "unknown error").strip()
                return False, f"Commit failed: {details}"

            # Push
            push = self._run("push", "-u", "origin", branch, "--force", check=False)
            if push.returncode != 0:
                details = (push.stderr or push.stdout or "unknown error").strip()
                return False, f"Push failed: {details}"

            return True, f"Branch '{branch}' pushed to origin"

        except subprocess.CalledProcessError as exc:
            details = (exc.stderr or exc.stdout or str(exc) or "unknown error").strip()
            return False, f"Git error: {details}"
