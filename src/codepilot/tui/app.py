from __future__ import annotations

import json
import queue
import threading

from textual.app import App, ComposeResult
from textual.containers import Container
from textual.screen import Screen
from textual.widgets import Footer, Header, Input, Log, Static


# ── Shared event queue that orchestrator writes to ───────────────────────────
event_queue: queue.Queue = queue.Queue()
command_queue: queue.Queue = queue.Queue()
AGENT_LOG_SELECTOR = "#agent-log"


class TaskInputScreen(Screen[str | None]):
    def compose(self) -> ComposeResult:
        yield Static("[b]New Task[/b]\n\nType a direct task and press Enter.\nPress Esc to cancel.")
        yield Input(placeholder="Example: Add CSV export endpoint for reports", id="task-input")

    def on_mount(self) -> None:
        self.query_one("#task-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        self.dismiss(value or None)

    def key_escape(self) -> None:
        self.dismiss(None)


class CodePilotApp(App):
    CSS = """
    Screen { layout: vertical; }

    #grid {
        height: 1fr;
        layout: grid;
        grid-size: 2 2;
        grid-columns: 1fr 1fr;
        grid-rows: 1fr 1fr;
        grid-gutter: 1;
        padding: 1;
    }

    .panel {
        border: solid #666666;
        padding: 1 2;
    }

    #agent-log { overflow-y: auto; }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("a", "approve", "Approve"),
        ("r", "reject", "Reject"),
        ("l", "inspect", "Inspect"),
        ("i", "new_task", "New task"),
        ("s", "skip_issue", "Skip issue"),
    ]

    def __init__(self, result_holder: list | None = None, **kwargs):
        super().__init__(**kwargs)
        self._result_holder = result_holder if result_holder is not None else []
        self._hitl_event: threading.Event = threading.Event()
        self._hitl_decision: list[bool] = [False]
        self._run_result: dict = {}
        self._latest_issues: list[dict] = []
        self._is_busy = False
        self._stop_worker: threading.Event = threading.Event()
        self._pending_pr_draft: dict = {}
        self._pending_hitl_files: list[str] = []

    # ── Layout ────────────────────────────────────────────────────────────────
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="grid"):
            yield Static(id="panel-issues", classes="panel")
            yield Static(id="panel-task", classes="panel")
            yield Log(id="agent-log", classes="panel", highlight=True)
            yield Static(id="panel-hitl", classes="panel")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#panel-issues", Static).update("[b]GitHub Issues[/b]\n\nFetching…")
        self.query_one("#panel-task", Static).update("[b]Active Task[/b]\n\nIdle")
        self.query_one("#panel-hitl", Static).update(
            "[b]Human Approval[/b]\n\nWaiting for task…\n\n[dim]Press A to approve, R to reject[/dim]"
        )
        self.set_interval(0.3, self._poll_events)

    # ── Event pump ────────────────────────────────────────────────────────────
    def _poll_events(self) -> None:
        try:
            while True:
                event = event_queue.get_nowait()
                self._handle_event(event)
        except queue.Empty:
            pass

    def _handle_event(self, event: dict) -> None:
        kind = event.get("kind", "")
        log = self.query_one(AGENT_LOG_SELECTOR, Log)
        task_panel = self.query_one("#panel-task", Static)
        issues_panel = self.query_one("#panel-issues", Static)
        hitl_panel = self.query_one("#panel-hitl", Static)

        if kind == "issues_fetched":
            issues = event.get("issues", [])
            self._latest_issues = issues
            lines = "\n".join(f"  #{i['id']} {i['title']}" for i in issues) or "  No assignable issues"
            issues_panel.update(f"[b]GitHub Issues[/b]\n\n{lines}")

        elif kind == "task_start":
            self._is_busy = True
            task_panel.update(
                f"[b]Active Task[/b]\n\n"
                f"Issue  : #{event.get('issue_id')} — {event.get('title', '')}\n"
                f"Type   : {event.get('task_type', '')}\n"
                f"State  : STARTING"
            )

        elif kind == "task_state":
            task_panel.update(
                f"[b]Active Task[/b]\n\n"
                f"Issue  : #{event.get('issue_id')} — {event.get('title', '')}\n"
                f"Type   : {event.get('task_type', '')}\n"
                f"State  : {event.get('state', '')}\n"
                f"Files  : {', '.join(event.get('files', []))}"
            )

        elif kind == "log":
            log.write_line(f"[{event.get('agent', 'sys')}] {event.get('message', '')}")

        elif kind == "hitl_request":
            pr = event.get("pr_draft", {})
            self._pending_pr_draft = pr
            self._pending_hitl_files = event.get("files", [])
            hitl_panel.update(
                f"[b]Human Approval[/b]\n\n"
                f"[yellow]AWAITING YOUR DECISION[/yellow]\n\n"
                f"Branch : {pr.get('branch', '')}\n"
                f"Title  : {pr.get('title', '')}\n"
                f"Files  : {', '.join(event.get('files', []))}\n\n"
                f"[b]Press A to approve, R to reject, L to inspect[/b]"
            )

        elif kind == "hitl_resolved":
            decision = event.get("approved", False)
            self._pending_pr_draft = {}
            self._pending_hitl_files = []
            hitl_panel.update(
                f"[b]Human Approval[/b]\n\n"
                f"{'[green]APPROVED[/green]' if decision else '[red]REJECTED[/red]'}\n\n"
                + (f"PR: {event.get('pr_url', '')}" if decision else "Branch not pushed.")
            )

        elif kind == "done":
            self._is_busy = False
            result = event.get("result", {})
            self._run_result = result
            pr_url = result.get("pr_url", "")
            task_panel.update(
                f"[b]Active Task[/b]\n\n"
                f"[green]DONE[/green]\n\n"
                f"Issue  : #{result.get('issue')}\n"
                f"Source : {result.get('fix_source', '')}\n"
                f"Tests  : passed\n"
                + (f"PR     : {pr_url}" if pr_url else "PR     : draft (not pushed)")
            )
            log.write_line(f"[orchestrator] Pipeline finished — {json.dumps({'pr_url': pr_url or 'none', 'state': result.get('state')})}")

    # ── HITL key bindings ─────────────────────────────────────────────────────
    def action_approve(self) -> None:
        self._hitl_decision[0] = True
        self._hitl_event.set()

    def action_reject(self) -> None:
        self._hitl_decision[0] = False
        self._hitl_event.set()

    def action_inspect(self) -> None:
        hitl_panel = self.query_one("#panel-hitl", Static)
        if not self._pending_pr_draft:
            self.query_one(AGENT_LOG_SELECTOR, Log).write_line("[ui] No pending approval item to inspect")
            return

        pr = self._pending_pr_draft
        body = str(pr.get("body", "")).strip()
        if len(body) > 500:
            body = body[:500].rstrip() + "..."
        reviewers = pr.get("reviewers", []) or []
        hitl_panel.update(
            f"[b]Human Approval - Inspect[/b]\n\n"
            f"Branch    : {pr.get('branch', '')}\n"
            f"Title     : {pr.get('title', '')}\n"
            f"Labels    : {', '.join(pr.get('labels', [])) or 'none'}\n"
            f"Reviewers : {', '.join(reviewers) or 'none'}\n"
            f"Files     : {', '.join(self._pending_hitl_files) or 'none'}\n\n"
            f"Body Preview:\n{body or 'none'}\n\n"
            f"[b]Press A to approve, R to reject[/b]"
        )

    def action_new_task(self) -> None:
        self.push_screen(TaskInputScreen(), self._on_new_task_entered)

    def _on_new_task_entered(self, value: str | None) -> None:
        if not value:
            return
        command_queue.put({"kind": "new_task", "text": value})
        self.query_one(AGENT_LOG_SELECTOR, Log).write_line(f"[ui] Queued manual task: {value}")

    def action_skip_issue(self) -> None:
        log = self.query_one(AGENT_LOG_SELECTOR, Log)
        if self._is_busy:
            log.write_line("[ui] Cannot skip while a task is running")
            return
        if not self._latest_issues:
            log.write_line("[ui] No issue to skip")
            return
        issue_id = self._latest_issues[0].get("id")
        command_queue.put({"kind": "skip_issue", "issue_id": issue_id})
        log.write_line(f"[ui] Requested skip for issue #{issue_id}")

    def hitl_approver(self, pr_draft: dict) -> bool:
        """Called by orchestrator on the worker thread — blocks until user presses A/R."""
        self._hitl_event.clear()
        self._hitl_decision[0] = False
        event_queue.put({
            "kind": "hitl_request",
            "pr_draft": pr_draft,
            "files": pr_draft.get("files_changed", []),
        })
        self._hitl_event.wait(timeout=300)  # 5 min timeout
        return self._hitl_decision[0]


def _run_orchestrator(app: CodePilotApp) -> None:
    """Worker thread: polls GitHub, handles UI commands, and runs tasks."""
    from src.codepilot.config import settings
    from src.codepilot.orchestrator import Orchestrator

    orch = Orchestrator(settings)

    def emit(msg: str, agent: str = "orchestrator") -> None:
        event_queue.put({"kind": "log", "agent": agent, "message": msg})

    pending_manual_tasks: list[str] = []
    idle_notice_shown = False

    while not app._stop_worker.is_set():
        while True:
            try:
                cmd = command_queue.get_nowait()
            except queue.Empty:
                break

            if cmd.get("kind") == "new_task":
                task_text = (cmd.get("text") or "").strip()
                if task_text:
                    pending_manual_tasks.append(task_text)
            elif cmd.get("kind") == "skip_issue":
                issue_id = cmd.get("issue_id")
                if isinstance(issue_id, int):
                    orch.skip_issue(issue_id)
                    emit(f"Skipping issue #{issue_id} for this session")

        try:
            issues = orch.fetch_processable_issues()
            event_queue.put({
                "kind": "issues_fetched",
                "issues": [{"id": i.issue_id, "title": i.title} for i in issues],
            })
        except Exception as exc:
            emit(f"Issue fetch failed: {exc}")
            issues = []

        issue = None
        if pending_manual_tasks:
            task_text = pending_manual_tasks.pop(0)
            issue = orch.build_manual_issue(task_text)
            emit(f"Starting manual task: {task_text}", agent="ui")
        elif issues:
            issue = issues[0]
            emit(f"Fetched issue #{issue.issue_id}: {issue.title}")

        if issue is None:
            if not idle_notice_shown:
                emit("No assignable issues found; waiting for next poll or manual task")
                idle_notice_shown = True

            if app._stop_worker.wait(timeout=max(1, int(settings.poll_interval_seconds))):
                break
            continue

        idle_notice_shown = False
        event_queue.put({"kind": "task_start", "issue_id": issue.issue_id, "title": issue.title, "task_type": ""})

        result = orch.run_issue(issue, hitl_approver=app.hitl_approver)

        task_type = result.get("task_type", "")
        state = result.get("state", "")
        event_queue.put({
            "kind": "task_state",
            "issue_id": issue.issue_id,
            "title": issue.title,
            "task_type": task_type,
            "state": state,
            "files": result.get("promoted_files", []),
        })

        for entry in result.get("log", []):
            emit(entry)

        if result.get("hitl_required"):
            event_queue.put({
                "kind": "hitl_resolved",
                "approved": result.get("hitl_approved", False),
                "pr_url": result.get("pr_url", ""),
            })

        event_queue.put({"kind": "done", "result": result})


def main() -> None:
    app = CodePilotApp()
    # Start orchestrator in background thread
    worker = threading.Thread(target=_run_orchestrator, args=(app,), daemon=True)
    worker.start()
    try:
        app.run()
    finally:
        app._stop_worker.set()


if __name__ == "__main__":
    main()
