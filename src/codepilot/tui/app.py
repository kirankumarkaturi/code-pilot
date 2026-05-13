from __future__ import annotations

import json
import queue
import threading

from textual.app import App, ComposeResult
from textual.containers import Container
from textual.widgets import Footer, Header, Log, Static


# ── Shared event queue that orchestrator writes to ───────────────────────────
event_queue: queue.Queue = queue.Queue()


class CodePilotApp(App):
    CSS = """
    Screen { layout: vertical; }

    #grid {
        height: 1fr;
        layout: grid;
        grid-size: 2 2;
        grid-columns: 1fr 2fr;
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

    BINDINGS = [("q", "quit", "Quit"), ("a", "approve", "Approve"), ("r", "reject", "Reject")]

    def __init__(self, result_holder: list | None = None, **kwargs):
        super().__init__(**kwargs)
        self._result_holder = result_holder if result_holder is not None else []
        self._hitl_event: threading.Event = threading.Event()
        self._hitl_decision: list[bool] = [False]
        self._run_result: dict = {}

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
        log = self.query_one("#agent-log", Log)
        task_panel = self.query_one("#panel-task", Static)
        issues_panel = self.query_one("#panel-issues", Static)
        hitl_panel = self.query_one("#panel-hitl", Static)

        if kind == "issues_fetched":
            issues = event.get("issues", [])
            lines = "\n".join(f"  #{i['id']} {i['title']}" for i in issues) or "  No assignable issues"
            issues_panel.update(f"[b]GitHub Issues[/b]\n\n{lines}")

        elif kind == "task_start":
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
            hitl_panel.update(
                f"[b]Human Approval[/b]\n\n"
                f"[yellow]AWAITING YOUR DECISION[/yellow]\n\n"
                f"Branch : {pr.get('branch', '')}\n"
                f"Title  : {pr.get('title', '')}\n"
                f"Files  : {', '.join(event.get('files', []))}\n\n"
                f"[b]Press A to approve, R to reject[/b]"
            )

        elif kind == "hitl_resolved":
            decision = event.get("approved", False)
            hitl_panel.update(
                f"[b]Human Approval[/b]\n\n"
                f"{'[green]APPROVED[/green]' if decision else '[red]REJECTED[/red]'}\n\n"
                + (f"PR: {event.get('pr_url', '')}" if decision else "Branch not pushed.")
            )

        elif kind == "done":
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

    def hitl_approver(self, pr_draft: dict) -> bool:
        """Called by orchestrator on the worker thread — blocks until user presses A/R."""
        event_queue.put({
            "kind": "hitl_request",
            "pr_draft": pr_draft,
            "files": pr_draft.get("files_changed", []),
        })
        self._hitl_event.wait(timeout=300)  # 5 min timeout
        return self._hitl_decision[0]


def _run_orchestrator(app: CodePilotApp) -> None:
    """Worker thread: runs the full pipeline and emits events to the TUI queue."""
    from src.codepilot.config import settings
    from src.codepilot.orchestrator import Orchestrator

    orch = Orchestrator(settings)

    # Emit issues
    try:
        issues = orch.github.fetch_open_issues()
        event_queue.put({
            "kind": "issues_fetched",
            "issues": [{"id": i.issue_id, "title": i.title} for i in issues],
        })
    except Exception:
        issues = []

    if not issues:
        event_queue.put({"kind": "log", "agent": "orchestrator", "message": "No assignable issues found"})
        event_queue.put({"kind": "done", "result": {"state": "IDLE"}})
        return

    issue = issues[0]
    event_queue.put({"kind": "task_start", "issue_id": issue.issue_id, "title": issue.title, "task_type": ""})

    def emit(msg: str, agent: str = "orchestrator") -> None:
        event_queue.put({"kind": "log", "agent": agent, "message": msg})

    emit(f"Fetched issue #{issue.issue_id}: {issue.title}")

    result = orch.run_once(hitl_approver=app.hitl_approver)

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
    app.run()


if __name__ == "__main__":
    main()
