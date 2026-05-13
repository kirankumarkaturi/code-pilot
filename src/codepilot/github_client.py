from __future__ import annotations

import json
from urllib import error, parse, request

from src.codepilot.models import Issue


class GitHubPRResult:
    def __init__(self, ok: bool, pr_url: str = "", pr_number: int = 0, message: str = "") -> None:
        self.ok = ok
        self.pr_url = pr_url
        self.pr_number = pr_number
        self.message = message


class GitHubClient:
    def __init__(
        self,
        owner: str,
        repo: str,
        token: str,
        use_dry_run: bool = True,
        max_complexity: int = 6,
    ) -> None:
        self.owner = owner
        self.repo = repo
        self.token = token
        self.use_dry_run = use_dry_run
        self.max_complexity = max_complexity

    def _dry_run_issues(self) -> list[Issue]:
        return [
            Issue(
                issue_id=42,
                title="Fix null pointer in claim parser",
                body="Intermittent crash in parser when optional field missing.",
                labels=["ai-assignable", "bug"],
            )
        ]

    def _estimate_complexity(self, title: str, body: str, labels: list[str]) -> int:
        for label in labels:
            lowered = label.lower().strip()
            if lowered.startswith("complexity:"):
                try:
                    return int(lowered.split(":", 1)[1])
                except ValueError:
                    pass

        # Simple heuristic when no explicit complexity label exists.
        text_len = len((title or "") + " " + (body or ""))
        score = 1 + (text_len // 280)
        if "bug" in " ".join(labels).lower():
            score += 1
        return max(1, min(10, score))

    def _is_assignable(self, issue_obj: dict) -> bool:
        labels = [lbl.get("name", "") for lbl in issue_obj.get("labels", []) if isinstance(lbl, dict)]
        has_ai_label = "ai-assignable" in {l.lower() for l in labels}
        is_unassigned = issue_obj.get("assignee") is None
        if not (has_ai_label or is_unassigned):
            return False

        complexity = self._estimate_complexity(
            issue_obj.get("title", ""),
            issue_obj.get("body") or "",
            labels,
        )
        return complexity <= self.max_complexity

    def _fetch_from_github(self) -> list[Issue]:
        if not self.owner or not self.repo or not self.token:
            return []

        query = parse.urlencode({"state": "open", "per_page": 50, "sort": "updated", "direction": "desc"})
        url = f"https://api.github.com/repos/{self.owner}/{self.repo}/issues?{query}"
        req = request.Request(url)
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        req.add_header("User-Agent", "codepilot-agent")

        with request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode("utf-8"))

        results: list[Issue] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            # GitHub issues API also returns PRs; skip them.
            if "pull_request" in item:
                continue
            if not self._is_assignable(item):
                continue

            labels = [lbl.get("name", "") for lbl in item.get("labels", []) if isinstance(lbl, dict)]
            assignee = item.get("assignee")
            assignee_login = assignee.get("login") if isinstance(assignee, dict) else None
            results.append(
                Issue(
                    issue_id=int(item.get("number", 0)),
                    title=item.get("title", ""),
                    body=item.get("body") or "",
                    labels=labels,
                    assignee=assignee_login,
                )
            )
        return results

    def fetch_open_issues(self) -> list[Issue]:
        if self.use_dry_run:
            return self._dry_run_issues()

        had_error = False
        try:
            issues = self._fetch_from_github()
        except (error.HTTPError, error.URLError, TimeoutError, json.JSONDecodeError):
            had_error = True
            issues = []

        if issues:
            return issues

        if had_error:
            # Fallback keeps the demo path alive if API limits/auth fail temporarily.
            return self._dry_run_issues()

        # Valid live response with no assignable issues.
        return []

    def create_pull_request(
        self,
        branch: str,
        base: str,
        title: str,
        body: str,
        labels: list[str],
        draft: bool = True,
    ) -> "GitHubPRResult":
        """Open a real draft PR on GitHub via REST API."""
        if not self.owner or not self.repo or not self.token:
            return GitHubPRResult(ok=False, message="Missing GitHub credentials")

        url = f"https://api.github.com/repos/{self.owner}/{self.repo}/pulls"
        payload = json.dumps({
            "title": title,
            "head": branch,
            "base": base,
            "body": body,
            "draft": draft,
        }).encode("utf-8")

        req = request.Request(url, data=payload, method="POST")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        req.add_header("Content-Type", "application/json")
        req.add_header("User-Agent", "codepilot-agent")

        try:
            with request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return GitHubPRResult(
                ok=True,
                pr_url=data.get("html_url", ""),
                pr_number=int(data.get("number", 0)),
                message="PR created",
            )
        except error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace")
            return GitHubPRResult(ok=False, message=f"HTTP {exc.code}: {body_text[:200]}")
        except (error.URLError, TimeoutError) as exc:
            return GitHubPRResult(ok=False, message=str(exc))
