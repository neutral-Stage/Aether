"""GraphRunner + GraphManager — schedule a TaskGraph onto the fleet (Phase 8).

The runner loops: skip-propagate → reap finished nodes (commit worktree, run the
deterministic gate) → schedule ready, non-conflicting nodes onto fleet sessions
(under a cost ceiling) → repeat until every node is terminal → synthesize into
one integration branch. GraphManager owns running graphs and runs each in a
background thread so the agent loop stays responsive.
"""
from __future__ import annotations

import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from . import synthesize
from . import worktree as wt
from .graph import TaskGraph, TaskNode


def run_gate(node: TaskNode, cwd: str) -> tuple[bool, str]:
    """Run a node's deterministic gate command in its worktree. No gate = pass."""
    if not node.gate_cmd:
        return True, ""
    try:
        res = subprocess.run(  # noqa: S602 — gate_cmd is agent-authored, runs in the node worktree
            node.gate_cmd, shell=True, cwd=cwd,
            capture_output=True, text=True, timeout=600,
        )
    except Exception as e:  # noqa: BLE001
        return False, f"gate error: {e}"
    if res.returncode == 0:
        return True, "gate passed"
    return False, f"gate failed (exit {res.returncode}): {(res.stderr or res.stdout)[-300:]}"


class GraphRunner:
    def __init__(
        self, manager: Any, graph: TaskGraph, workspace: str | Path, *,
        base_ref: str | None = None, budget_usd: float | None = None,
        poll_sec: float = 0.5, on_event: Callable[[dict], None] | None = None,
    ) -> None:
        self.manager = manager
        self.graph = graph
        self.workspace = str(workspace)
        self.budget_usd = budget_usd
        self.poll_sec = poll_sec
        self.on_event = on_event
        self.root = wt.repo_root(workspace)
        self.base_ref = base_ref or (wt.current_ref(self.root) if self.root else "HEAD")

    def _emit(self, kind: str, **extra: Any) -> None:
        if self.on_event is None:
            return
        try:
            self.on_event({"type": "graph", "graph_id": self.graph.graph_id,
                           "event": kind, **extra})
        except Exception:  # noqa: BLE001
            pass

    def run(self) -> TaskGraph:
        self.graph.status = "running"
        try:
            self.graph.validate()
        except ValueError as e:
            self.graph.status = "failed"
            self.graph.result = f"invalid graph: {e}"
            self._emit("failed", error=str(e))
            return self.graph
        self._emit("started", nodes=len(self.graph.nodes))

        while not self.graph.is_complete():
            if self.graph.cancelled:
                # Reap first: the STOP bridge already killed running sessions, so
                # finalize those nodes (done/failed) before stranding the pending
                # ones — else they'd freeze at 'running' and drop from integration.
                self._reap()
                self._skip_stranded(reason="cancelled by STOP")
                break
            self.graph.propagate_skips()
            self._reap()
            self._schedule()
            if self.graph.is_complete():
                break
            if not self.graph.running():
                # Nothing running and nothing schedulable → over budget or a
                # dependency/conflict deadlock. Strand the rest so we terminate.
                self._skip_stranded()
                break
            time.sleep(self.poll_sec)

        self._finish()
        return self.graph

    def _skip_stranded(self, reason: str | None = None) -> None:
        reason = reason or ("budget exhausted" if self._over_budget()
                            else "unschedulable (dependency or conflict deadlock)")
        for node in self.graph.nodes.values():
            if node.status == "pending":
                node.status = "skipped"
                node.error = reason
                self._emit("node_failed", node=node.id, error=reason)

    def _over_budget(self) -> bool:
        return self.budget_usd is not None and self.manager.total_cost() >= self.budget_usd

    def _schedule(self) -> None:
        if self._over_budget():
            return
        running_ids = [n.id for n in self.graph.running()]
        for node in self.graph.ready(running_ids):
            try:
                session = self.manager.spawn(
                    agent_type=node.agent_type, prompt=node.prompt,
                    workspace=self.workspace, label=node.title[:40], isolate=True,
                )
            except ValueError:
                return  # fleet full / over cap → retry next tick
            node.session_id = session.session_id
            node.status = "running"
            self._emit("node_running", node=node.id, session_id=session.session_id)

    def _reap(self) -> None:
        for node in self.graph.running():
            session = self.manager.resolve(node.session_id or "")
            if session is None:
                node.status = "failed"
                node.error = "session lost"
                self._emit("node_failed", node=node.id, error=node.error)
                continue
            # A claude turn ends in 'awaiting_input' (still active); treat a
            # one-shot node as complete then. Other actives keep running.
            if session.is_active() and session.state != "awaiting_input":
                continue
            node.branch = session.worktree_branch
            node.result = session.result_summary
            # An errored final state, OR a Claude turn that ended in awaiting_input
            # but reported is_error, is a node failure — don't merge its branch.
            if session.state in ("error", "stopped", "timeout") or getattr(
                session, "last_turn_error", False
            ):
                node.status = "failed"
                node.error = session.result_summary or session.state
                self._emit("node_failed", node=node.id, error=node.error)
                continue
            cwd = session.worktree_dir or session.workspace
            if node.branch and session.worktree_dir:
                wt.commit_all(session.worktree_dir, f"aether: {node.title[:60]}")
            ok, detail = run_gate(node, cwd)
            if ok:
                node.status = "done"
                self._emit("node_done", node=node.id)
            else:
                node.status = "failed"
                node.error = detail
                self._emit("node_failed", node=node.id, error=detail)

    def _finish(self) -> None:
        integ: dict[str, Any] = {"branch": None}
        if self.root:
            branches = [n.branch for n in self.graph.done_in_order() if n.branch]
            integ = synthesize.integrate(
                self.root, self.base_ref, branches, self.graph.graph_id)
            self.graph.integration_branch = integ.get("branch")
        self.graph.result = synthesize.summarize(self.graph.to_dict(), integ)
        self.graph.status = "failed" if self.graph.has_failures() else "done"
        self._emit("complete", result=self.graph.result, status=self.graph.status)


class GraphManager:
    """Singleton owning submitted graphs; runs each on its own thread."""

    _instance: GraphManager | None = None
    _class_lock = threading.Lock()

    def __init__(self) -> None:
        self._graphs: dict[str, TaskGraph] = {}
        self._lock = threading.Lock()
        self._event_sink: Callable[[dict], None] | None = None

    @classmethod
    def get(cls) -> GraphManager:
        with cls._class_lock:
            if cls._instance is None:
                cls._instance = GraphManager()
            return cls._instance

    @classmethod
    def reset(cls) -> None:
        with cls._class_lock:
            cls._instance = None

    def set_event_sink(self, fn: Callable[[dict], None] | None) -> None:
        with self._lock:
            self._event_sink = fn

    def _emit(self, event: dict) -> None:
        with self._lock:
            sink = self._event_sink
        if sink is not None:
            try:
                sink(event)
            except Exception:  # noqa: BLE001
                pass

    def submit(
        self, goal: str, nodes: list[TaskNode], workspace: str, *,
        manager: Any = None, budget_usd: float | None = None,
        run_async: bool = True,
    ) -> TaskGraph:
        from .manager import SessionManager
        graph = TaskGraph(uuid.uuid4().hex[:8], goal, nodes)
        graph.validate()  # raise before we register anything
        with self._lock:
            self._graphs[graph.graph_id] = graph
        runner = GraphRunner(
            manager or SessionManager.get(), graph, workspace,
            budget_usd=budget_usd, on_event=self._emit,
        )

        def _guarded_run() -> None:
            # A crash in the runner thread must not wedge the graph at 'running'.
            try:
                runner.run()
            except Exception as e:  # noqa: BLE001
                graph.status = "failed"
                graph.result = f"runner crashed: {e}"
                self._emit({"type": "graph", "graph_id": graph.graph_id,
                            "event": "failed", "error": str(e)})

        if run_async:
            threading.Thread(target=_guarded_run, daemon=True,
                             name=f"graph-{graph.graph_id}").start()
        else:
            _guarded_run()
        return graph

    def cancel_all(self) -> int:
        """Signal every running graph to strand its pending nodes and finish."""
        with self._lock:
            graphs = [g for g in self._graphs.values() if g.status == "running"]
        for g in graphs:
            g.cancelled = True
        return len(graphs)

    def resolve(self, graph_id: str) -> TaskGraph | None:
        with self._lock:
            g = self._graphs.get(graph_id)
            if g:
                return g
            for graph in self._graphs.values():
                if graph.graph_id.startswith(graph_id):
                    return graph
        return None

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [g.to_dict() for g in self._graphs.values()]
