# Agent Fleet, MCP Server & Multi-Run

Aether can orchestrate **multiple AI coding agents at once** — spawn them, steer
them, watch their output, and roll up their cost — while continuing to drive the
GUI. This is the "supervising orchestrator" model: Aether picks the right tool
for a job, hands it off, and keeps the human in the loop.

## Fleet — concurrent agent sessions

Enabled by default (`fleet.enabled: true`). Sessions run as background
subprocesses and **survive between Aether runs** (they live outside the run
registry), so "spawn a Claude Code to fix the tests, then let me know" works.

### Agent types
| type | backing CLI | steering |
|------|-------------|----------|
| `claude` | `claude -p --output-format stream-json` | full multi-turn (stdin) |
| `codex` | `codex exec --json` | respawn-with-digest |
| `opencode` | `opencode run` | respawn-with-digest |
| `terminal` / `kilo` | PTY | interactive (send keystrokes) |

### Tools (voice- or text-driveable)
- `spawn_agent(agent_type, prompt, workspace, label, isolate)` — start a session.
- `send_to_agent(session_id|label, text)` — steer it.
- `get_agent_output(session_id, tail_lines)` — read its output.
- `list_agents()` / `stop_agent(session_id)` / `wait_for_agent(session_id, timeout_sec)`.

### HTTP API
`GET /fleet`, `GET /fleet/{id}`, `GET /fleet/{id}/output?since_seq=`,
`POST /fleet/spawn|{id}/send|{id}/stop|stop_all`. Fleet events (state changes,
completion) stream over the sidecar SSE channel; completion also emits a spoken
summary.

### Safety
- Per-session `cost_cap_usd` and `session_timeout_sec` watchdogs; global
  `max_sessions`.
- Git **worktree isolation** per session (`fleet.worktrees.enabled`); falls back
  to in-place with a warning, or refuses if `require_isolation`.
- `--dangerously-skip-permissions` is never passed unless
  `fleet.allow_bypass_permissions` **and** the session is in an isolated worktree.
- Every spawn/send/stop is written to the signed audit log.

### Config (`config.yaml` → `fleet:`)
`enabled, max_sessions, output_buffer_lines, session_timeout_sec, cost_cap_usd,
claude_permission_mode, claude_allowed_tools, env_allowlist, worktrees, idle_detect_sec`.

## Task graphs — multi-agent orchestration (Phase 8)

The fleet gives you N agents; a **task graph** coordinates them. Decompose a goal
into nodes with `depends_on` edges and the file `paths` each node touches:

- **Parallel only where safe** — independent nodes whose **declared path scopes**
  don't overlap run at once; a node whose scope overlaps a running node's
  serializes (conservative: an undeclared/empty scope is treated as repo-wide).
  At merge time, `git merge-tree` additionally detects real conflicts before
  integration. Path scopes are trusted — a node that edits files outside its
  declared `paths` can still collide.
- **Isolated + gated** — each node runs in its own git worktree; on completion
  its diff is committed and its optional `gate_cmd` (e.g. `pytest -q`) must pass,
  else the node fails.
- **Synthesized** — all successful node branches merge, in dependency order, into
  one `aether/integration/<graph_id>` branch. Conflicting branches are **reported,
  not force-merged** — you review them. The user's checked-out branch is untouched.
- **Failure propagation** — a failed node skips its dependents; a critic can be a
  regular node that `depends_on` a code node.
- **Budget** — scheduling stops past `graph.default_budget_usd` (config).

Tools: `spawn_graph(goal, nodes=[{id, prompt, depends_on, paths, gate_cmd, ...}])`
and `get_graph(graph_id?)`. HTTP: `GET /fleet/graphs`, `GET /fleet/graphs/{id}`;
node/complete events stream over SSE (completion also speaks a summary). The
Swift `GraphView` renders each graph's nodes, status, deps, and integration branch.

## Aether as an MCP server

So fleet agents can **see and drive the screen through Aether** (with the policy
gate + audit applied), Aether exposes a curated tool subset over MCP.

- Enable: `mcp_server.enabled: true` (off by default — it grants GUI control to
  subprocesses). Endpoint: `POST /mcp` on the sidecar (localhost, auth-gated).
- Inject into fleet Claude sessions: `mcp_server.expose_to_fleet: true` — the
  manager writes a temporary `--mcp-config` (with the sidecar bearer token if
  `AETHER_SIDECAR_TOKEN` is set).
- Exposed tools (`mcp_server.expose_tools`, all read/reversible):
  `get_screen_context, screenshot, analyze_screen, click, type_text, open_app`.
  Destructive-impact calls are always blocked on this path.

## Multi-run sidecar

By default the sidecar runs **one agent run at a time** (`/run` returns 409 when
busy) — unchanged behaviour. Raise `sidecar.max_concurrent_runs` to let runs
overlap (e.g. issue a quick command while a long task is in flight).

- `GET /runs` — all tracked runs (active + bounded history), newest last.
- `GET /runs/{id}` — one run's status/result/world snapshot.
- `GET /status` — most-recent run (back-compat) plus `active_runs` /
  `max_concurrent_runs`.
- `POST /stop` — **global panic button**: always halts *all* in-flight runs (the
  STOP signal is process-wide). An optional `{"run_id": "..."}` narrows which run
  rows are marked stopped, but the halt is still global by design.

Each run gets its own HUD binding, so concurrent runs don't cross streams. Note:
careful-mode confirmations route through a process-global broadcaster — with
`max_concurrent_runs > 1` and careful mode on, confirmations are best-effort.
