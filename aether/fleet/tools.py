"""Fleet tools — spawn/steer/monitor/stop concurrent agent sessions."""
from __future__ import annotations

import time

from .manager import AGENT_TYPES, SessionManager
from .session import AgentSession


def _fmt_status(s: dict) -> str:
    line = (
        f"[{s['session_id']}] {s['agent_type']} \"{s['label']}\" state={s['state']} "
        f"elapsed={s['elapsed_sec']}s cost=${s['cost_usd']:.4f}"
    )
    if s.get("worktree_branch"):
        line += f" branch={s['worktree_branch']}"
    if s.get("result_summary"):
        line += f"\n  result: {s['result_summary'][:300]}"
    elif s.get("last_output"):
        line += f"\n  last: {s['last_output'][:200]}"
    return line


def _fmt_tail(session: AgentSession, n: int) -> str:
    events = session.tail(n)
    if not events:
        return "(no output yet)"
    return "\n".join(f"{e.kind}: {e.content}" for e in events)


def _h_spawn_agent(args: dict, _ctx) -> str:
    mgr = SessionManager.get()
    try:
        session = mgr.spawn(
            agent_type=args["agent_type"],
            prompt=args["prompt"],
            workspace=args.get("workspace"),
            label=args.get("label"),
            isolate=args.get("isolate"),
            timeout_sec=args.get("timeout_sec"),
        )
    except ValueError as e:
        return f"ERROR: {e}"
    return (
        f"Spawned {session.agent_type} session [{session.session_id}] "
        f"label=\"{session.label}\" in {session.workspace}. "
        "Use get_agent_output/wait_for_agent to monitor, send_to_agent to steer."
    )


def _h_send_to_agent(args: dict, _ctx) -> str:
    mgr = SessionManager.get()
    try:
        ok = mgr.send(args["session_id"], args["text"])
    except ValueError as e:
        return f"ERROR: {e}"
    if not ok:
        return (
            "Could not deliver (session busy, finished, or transport does not "
            "support steering). Check get_agent_output."
        )
    return "Sent."


def _h_get_agent_output(args: dict, _ctx) -> str:
    session = SessionManager.get().resolve(args["session_id"])
    if session is None:
        return f"ERROR: no session matching {args['session_id']!r}"
    n = int(args.get("tail_lines", 40))
    return _fmt_status(session.status_dict()) + "\n--- output ---\n" + _fmt_tail(session, n)


def _h_list_agents(_args: dict, _ctx) -> str:
    sessions = SessionManager.get().list()
    if not sessions:
        return "No agent sessions."
    return "\n".join(_fmt_status(s) for s in sessions)


def _h_stop_agent(args: dict, _ctx) -> str:
    try:
        session = SessionManager.get().stop(args["session_id"])
    except ValueError as e:
        return f"ERROR: {e}"
    return f"Stopped [{session.session_id}] \"{session.label}\" (state={session.state})."


def _h_wait_for_agent(args: dict, _ctx) -> str:
    session = SessionManager.get().resolve(args["session_id"])
    if session is None:
        return f"ERROR: no session matching {args['session_id']!r}"
    # capped so the agent loop stays responsive; poll again for longer waits
    timeout = min(float(args.get("timeout_sec", 60)), 120.0)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not session.is_active() or session.state == "awaiting_input":
            break
        time.sleep(0.5)
    status = _fmt_status(session.status_dict())
    if session.is_active() and session.state not in ("awaiting_input",):
        status = f"(still {session.state} after {int(timeout)}s)\n" + status
    return status + "\n--- output ---\n" + _fmt_tail(session, 20)


def _h_spawn_graph(args: dict, _ctx) -> str:
    from .graph import TaskNode
    from .graph_runner import GraphManager
    raw_nodes = args.get("nodes") or []
    if not raw_nodes:
        return "ERROR: nodes is required (a list of {id, title, prompt, depends_on, paths})."
    # 'terminal' nodes get no worktree and complete via an idle heuristic with no
    # success/diff — they can't be committed, gated, or integrated. Reject them.
    bad = [n.get("id") for n in raw_nodes if str(n.get("agent_type", "claude")) == "terminal"]
    if bad:
        return (f"ERROR: terminal agents can't be graph nodes ({', '.join(map(str, bad))}) "
                "— they produce no integratable diff. Use claude/codex/opencode/cursor/kilo.")
    try:
        nodes = [
            TaskNode(
                id=str(n["id"]), title=str(n.get("title", n["id"])),
                prompt=str(n["prompt"]), agent_type=str(n.get("agent_type", "claude")),
                depends_on=list(n.get("depends_on") or []),
                paths=list(n.get("paths") or []),
                gate_cmd=n.get("gate_cmd"), difficulty=str(n.get("difficulty", "medium")),
            )
            for n in raw_nodes
        ]
    except (KeyError, TypeError) as e:
        return f"ERROR: bad node spec: {e}"
    budget = args.get("budget_usd")
    if budget is None:
        from ..core.config import load_config
        cfg_budget = (load_config(validate=False).get("graph") or {}).get("default_budget_usd")
        budget = cfg_budget if cfg_budget else None
    try:
        graph = GraphManager.get().submit(
            goal=str(args.get("goal", "")), nodes=nodes,
            workspace=str(args.get("workspace") or "."),
            budget_usd=budget,
        )
    except ValueError as e:
        return f"ERROR: {e}"
    return (
        f"Submitted graph [{graph.graph_id}] with {len(nodes)} nodes. "
        "Independent, non-conflicting nodes run in parallel; each code node is "
        "committed, gated, and synthesized into one integration branch. "
        "Monitor with get_graph."
    )


def _h_get_graph(args: dict, _ctx) -> str:
    from .graph_runner import GraphManager
    gid = args.get("graph_id")
    mgr = GraphManager.get()
    if not gid:
        graphs = mgr.list()
        if not graphs:
            return "No task graphs."
        return "\n".join(
            f"[{g['graph_id']}] {g['status']}: {g['goal'][:60]} "
            f"({g['counts']['done']} done / {len(g['nodes'])} nodes)"
            for g in graphs
        )
    graph = mgr.resolve(str(gid))
    if graph is None:
        return f"ERROR: no graph matching {gid!r}"
    d = graph.to_dict()
    lines = [f"Graph [{d['graph_id']}] {d['status']} — {d['goal'][:80]}"]
    for n in d["nodes"]:
        deps = f" ⇐ {','.join(n['depends_on'])}" if n["depends_on"] else ""
        lines.append(f"  [{n['status']}] {n['title']}{deps}"
                     + (f" — {n['error'][:80]}" if n["error"] else ""))
    if d["result"]:
        lines.append(f"Result: {d['result']}")
    return "\n".join(lines)


def register_fleet_tools(registry) -> None:
    """Register fleet tools (permission='agents', gated by capabilities.agents)."""
    tools = [
        dict(
            name="spawn_graph",
            description=(
                "Orchestrate a MULTI-AGENT task graph. Decompose a goal into nodes "
                "with `depends_on` edges and file `paths` each node will touch. "
                "Independent, non-path-conflicting nodes run in PARALLEL; dependent "
                "ones wait. Each node runs in an isolated git worktree, is committed "
                "+ gated (optional `gate_cmd`, e.g. a test command), and all results "
                "are merged into one `aether/integration/<id>` branch (conflicts "
                "reported, not force-merged). Use for anything with parallelizable "
                "sub-tasks; use spawn_agent for a single agent."
            ),
            json_schema={
                "type": "object",
                "properties": {
                    "goal": {"type": "string"},
                    "workspace": {"type": "string", "description": "Project dir (git repo)"},
                    "budget_usd": {"type": "number", "description": "Stop scheduling past this fleet cost"},
                    "nodes": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "title": {"type": "string"},
                                "prompt": {"type": "string"},
                                "agent_type": {"type": "string", "enum": list(AGENT_TYPES)},
                                "depends_on": {"type": "array", "items": {"type": "string"}},
                                "paths": {"type": "array", "items": {"type": "string"},
                                          "description": "File globs this node touches (for conflict avoidance)"},
                                "gate_cmd": {"type": "string", "description": "Deterministic check, e.g. 'pytest -q'"},
                                "difficulty": {"type": "string", "enum": ["low", "medium", "high"]},
                            },
                            "required": ["id", "prompt"],
                        },
                    },
                },
                "required": ["nodes"],
            },
            impact="reversible",
            handler=_h_spawn_graph,
        ),
        dict(
            name="get_graph",
            description="Status of a task graph (or all graphs when graph_id omitted).",
            json_schema={
                "type": "object",
                "properties": {"graph_id": {"type": "string"}},
            },
            impact="read",
            handler=_h_get_graph,
        ),
        dict(
            name="spawn_agent",
            description=(
                "Spawn a concurrent agent session: a Claude Code / Codex / OpenCode / "
                "Kilo coding agent, or a persistent terminal. Runs in the background — "
                "you keep working while it does. Prefer isolate=true (git worktree) for "
                "code changes. Use for long or parallel work; delegate_to_coder for "
                "quick one-shots."
            ),
            json_schema={
                "type": "object",
                "properties": {
                    "agent_type": {"type": "string", "enum": list(AGENT_TYPES)},
                    "prompt": {"type": "string", "description": "Task spec or initial command"},
                    "workspace": {"type": "string", "description": "Project dir (default: cwd)"},
                    "label": {"type": "string", "description": "Short human label"},
                    "isolate": {"type": "boolean", "description": "Git worktree isolation"},
                    "timeout_sec": {"type": "integer"},
                },
                "required": ["agent_type", "prompt"],
            },
            impact="reversible",
            handler=_h_spawn_agent,
        ),
        dict(
            name="send_to_agent",
            description="Send a follow-up instruction to a running agent session.",
            json_schema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "Session id or label"},
                    "text": {"type": "string"},
                },
                "required": ["session_id", "text"],
            },
            impact="reversible",
            handler=_h_send_to_agent,
        ),
        dict(
            name="get_agent_output",
            description="Status + recent output of an agent session.",
            json_schema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "tail_lines": {"type": "integer", "description": "default 40"},
                },
                "required": ["session_id"],
            },
            impact="read",
            handler=_h_get_agent_output,
        ),
        dict(
            name="list_agents",
            description="List all agent sessions (active and finished) with state and cost.",
            json_schema={"type": "object", "properties": {}},
            impact="read",
            handler=_h_list_agents,
        ),
        dict(
            name="stop_agent",
            description="Stop an agent session (SIGINT→SIGTERM→SIGKILL).",
            json_schema={
                "type": "object",
                "properties": {"session_id": {"type": "string"}},
                "required": ["session_id"],
            },
            impact="reversible",
            handler=_h_stop_agent,
        ),
        dict(
            name="wait_for_agent",
            description=(
                "Block until a session finishes or awaits input (timeout ≤120s), "
                "then return status + output tail."
            ),
            json_schema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "timeout_sec": {"type": "integer", "description": "max 120"},
                },
                "required": ["session_id"],
            },
            impact="read",
            handler=_h_wait_for_agent,
        ),
    ]
    for t in tools:
        registry.register_dynamic(permission="agents", **t)
