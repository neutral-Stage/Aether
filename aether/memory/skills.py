"""Skill / macro memory — distill successful traces into reusable skills (§6.6, FR-25)."""
from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np

from .embeddings import HashEmbedder, cosine_similarity, create_embedder

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SKILLS_DB = ROOT / "data" / "skills.db"

_embed = HashEmbedder().embed
_cosine = cosine_similarity


@dataclass
class Skill:
    id: int
    name: str
    description: str
    goal_pattern: str
    parameters: list[str]
    steps: list[dict[str, Any]]
    success_count: int
    score: float = 0.0
    created_at: float = 0.0
    updated_at: float = 0.0


@dataclass
class SkillReplayResult:
    skill_id: int
    goal: str
    steps_executed: int
    observations: list[str]
    success: bool
    error: str | None = None


class SkillStore:
    """Persist parameterized multi-step skills learned from successful runs."""

    def __init__(
        self,
        db_path: str | Path | None = None,
        *,
        embedding_provider: str = "hash",
        openai_api_key: str | None = None,
    ):
        self.db_path = Path(db_path) if db_path else DEFAULT_SKILLS_DB
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._embedder = create_embedder(
            embedding_provider,
            openai_api_key=openai_api_key,
        )
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS skills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT NOT NULL,
                goal_pattern TEXT NOT NULL,
                parameters TEXT NOT NULL,
                steps TEXT NOT NULL,
                embedding BLOB NOT NULL,
                success_count INTEGER NOT NULL DEFAULT 1,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
        """)
        self._conn.commit()

    def list_skills(self, limit: int = 100) -> list[Skill]:
        rows = self._conn.execute(
            "SELECT id, name, description, goal_pattern, parameters, steps, "
            "embedding, success_count, created_at, updated_at FROM skills "
            "ORDER BY success_count DESC, updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._row_to_skill(row) for row in rows]

    def get_skill(self, skill_id: int) -> Skill | None:
        row = self._conn.execute(
            "SELECT id, name, description, goal_pattern, parameters, steps, "
            "embedding, success_count, created_at, updated_at FROM skills WHERE id = ?",
            (skill_id,),
        ).fetchone()
        if not row:
            return None
        return self._row_to_skill(row)

    def distill_from_trace(
        self,
        goal: str,
        tool_trace: list[dict[str, Any]],
    ) -> int | None:
        """Create or update a skill from a successful tool trace."""
        if len(tool_trace) < 2:
            return None

        params = _extract_parameters(goal, tool_trace)
        steps = _parameterize_steps(tool_trace, params)
        name = _skill_name_from_goal(goal)
        description = f"Learned skill for: {goal[:120]}"
        emb = self._embedder.embed(f"{goal} {name} {description}")

        existing = self._conn.execute(
            "SELECT id, success_count FROM skills WHERE name = ?", (name,)
        ).fetchone()
        now = time.time()
        if existing:
            self._conn.execute(
                "UPDATE skills SET description=?, goal_pattern=?, parameters=?, "
                "steps=?, embedding=?, success_count=?, updated_at=? WHERE id=?",
                (
                    description,
                    goal,
                    json.dumps(params),
                    json.dumps(steps),
                    emb.tobytes(),
                    int(existing["success_count"]) + 1,
                    now,
                    existing["id"],
                ),
            )
            self._conn.commit()
            return int(existing["id"])

        cur = self._conn.execute(
            "INSERT INTO skills (name, description, goal_pattern, parameters, steps, "
            "embedding, success_count, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)",
            (
                name,
                description,
                goal,
                json.dumps(params),
                json.dumps(steps),
                emb.tobytes(),
                now,
                now,
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def retrieve(self, goal: str, limit: int = 3) -> list[Skill]:
        q_emb = self._embedder.embed(goal)
        rows = self._conn.execute(
            "SELECT id, name, description, goal_pattern, parameters, steps, "
            "embedding, success_count, created_at, updated_at FROM skills"
        ).fetchall()
        scored: list[Skill] = []
        for row in rows:
            emb = np.frombuffer(row["embedding"], dtype=np.float32)
            if emb.shape[0] != q_emb.shape[0]:
                if emb.shape[0] == _embed("").shape[0]:
                    score = cosine_similarity(_embed(goal), emb)
                else:
                    score = 0.0
            else:
                score = cosine_similarity(q_emb, emb)
            scored.append(self._row_to_skill(row, score=score))
        scored.sort(key=lambda s: (s.score, s.success_count), reverse=True)
        return [s for s in scored[:limit] if s.score >= 0.08]

    def substitute_parameters(
        self,
        steps: list[dict[str, Any]],
        args: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Replace {{paramN}} placeholders and named keys in step args."""
        resolved: list[dict[str, Any]] = []
        for step in steps:
            tool = step.get("tool", "")
            raw_args = dict(step.get("args") or {})
            new_args: dict[str, Any] = {}
            for key, val in raw_args.items():
                if isinstance(val, str):
                    new_args[key] = _apply_param_substitution(val, args)
                else:
                    new_args[key] = val
            resolved.append({"tool": tool, "args": new_args})
        return resolved

    def build_replay_goal(self, skill: Skill, args: dict[str, Any]) -> str:
        """Natural-language goal for orchestrator replay with substituted params."""
        goal = skill.goal_pattern
        for i, param in enumerate(skill.parameters, 1):
            placeholder = f"{{{{param{i}}}}}"
            val = args.get(f"param{i}") or args.get(param) or args.get(str(i))
            if val is not None:
                goal = goal.replace(param, str(val)).replace(placeholder, str(val))
        for key, val in args.items():
            goal = goal.replace(f"{{{{{key}}}}}", str(val))
        return (
            f"Replay learned skill '{skill.name}' (id={skill.id}): {goal}\n"
            f"Follow these steps in order:\n"
            + "\n".join(
                f"{i}. {s.get('tool')}({json.dumps(s.get('args', {}), ensure_ascii=False)})"
                for i, s in enumerate(
                    self.substitute_parameters(skill.steps, args), 1
                )
            )
        )

    def replay(
        self,
        skill_id: int,
        args: dict[str, Any],
        *,
        dispatch: Any,
        ctx: Any,
        skip_tools: frozenset[str] = frozenset({"finish", "get_screen_context"}),
        policy: Any | None = None,
        registry: Any | None = None,
        confirm: Callable[[str, str, dict[str, Any]], bool] | None = None,
    ) -> SkillReplayResult:
        """Execute skill steps through the tool registry dispatch function.

        When ``policy`` and ``registry`` are provided, each step is checked
        against the policy gate (permissions, path rules, confirmations).
        """
        skill = self.get_skill(skill_id)
        if not skill:
            return SkillReplayResult(
                skill_id=skill_id,
                goal="",
                steps_executed=0,
                observations=[],
                success=False,
                error=f"skill {skill_id} not found",
            )

        steps = self.substitute_parameters(skill.steps, args)
        observations: list[str] = []
        executed = 0
        for step in steps:
            tool = step.get("tool", "")
            if not tool or tool in skip_tools:
                continue
            step_args = step.get("args") or {}
            if policy is not None and registry is not None:
                spec = registry.get(tool)
                if spec is None:
                    return SkillReplayResult(
                        skill_id=skill_id,
                        goal=skill.goal_pattern,
                        steps_executed=executed,
                        observations=observations,
                        success=False,
                        error=f"unknown tool: {tool}",
                    )
                if not policy.allows_tool(spec):
                    return SkillReplayResult(
                        skill_id=skill_id,
                        goal=skill.goal_pattern,
                        steps_executed=executed,
                        observations=observations,
                        success=False,
                        error=f"permission denied for {tool}",
                    )
                if tool == "run_shell" and not policy.allows_shell_path(
                    step_args.get("command", "")
                ):
                    return SkillReplayResult(
                        skill_id=skill_id,
                        goal=skill.goal_pattern,
                        steps_executed=executed,
                        observations=observations,
                        success=False,
                        error="shell command blocked: path outside approved roots",
                    )
                if policy.requires_confirm(spec, step_args):
                    if confirm is None:
                        return SkillReplayResult(
                            skill_id=skill_id,
                            goal=skill.goal_pattern,
                            steps_executed=executed,
                            observations=observations,
                            success=False,
                            error=f"confirmation required for {tool}",
                        )
                    desc = registry.describe_call(tool, step_args)
                    if not confirm(tool, desc, step_args):
                        return SkillReplayResult(
                            skill_id=skill_id,
                            goal=skill.goal_pattern,
                            steps_executed=executed,
                            observations=observations,
                            success=False,
                            error=f"user declined {tool}",
                        )
            try:
                obs = dispatch(tool, step_args, ctx)
                observations.append(str(obs))
                executed += 1
                if str(obs).startswith("ERROR"):
                    return SkillReplayResult(
                        skill_id=skill_id,
                        goal=skill.goal_pattern,
                        steps_executed=executed,
                        observations=observations,
                        success=False,
                        error=str(obs),
                    )
            except Exception as exc:  # noqa: BLE001
                return SkillReplayResult(
                    skill_id=skill_id,
                    goal=skill.goal_pattern,
                    steps_executed=executed,
                    observations=observations,
                    success=False,
                    error=str(exc),
                )

        return SkillReplayResult(
            skill_id=skill_id,
            goal=skill.goal_pattern,
            steps_executed=executed,
            observations=observations,
            success=True,
        )

    async def replay_async(
        self,
        skill_id: int,
        args: dict[str, Any],
        *,
        dispatch: Any,
        ctx: Any,
        skip_tools: frozenset[str] = frozenset({"finish", "get_screen_context"}),
        policy: Any | None = None,
        registry: Any | None = None,
        confirm: Callable[[str, str, dict[str, Any]], Any] | None = None,
    ) -> SkillReplayResult:
        """Async replay with optional async confirmation callback."""
        skill = self.get_skill(skill_id)
        if not skill:
            return SkillReplayResult(
                skill_id=skill_id,
                goal="",
                steps_executed=0,
                observations=[],
                success=False,
                error=f"skill {skill_id} not found",
            )

        steps = self.substitute_parameters(skill.steps, args)
        observations: list[str] = []
        executed = 0
        for step in steps:
            tool = step.get("tool", "")
            if not tool or tool in skip_tools:
                continue
            step_args = step.get("args") or {}
            if policy is not None and registry is not None:
                spec = registry.get(tool)
                if spec is None:
                    return SkillReplayResult(
                        skill_id=skill_id,
                        goal=skill.goal_pattern,
                        steps_executed=executed,
                        observations=observations,
                        success=False,
                        error=f"unknown tool: {tool}",
                    )
                if not policy.allows_tool(spec):
                    return SkillReplayResult(
                        skill_id=skill_id,
                        goal=skill.goal_pattern,
                        steps_executed=executed,
                        observations=observations,
                        success=False,
                        error=f"permission denied for {tool}",
                    )
                if tool == "run_shell" and not policy.allows_shell_path(
                    step_args.get("command", "")
                ):
                    return SkillReplayResult(
                        skill_id=skill_id,
                        goal=skill.goal_pattern,
                        steps_executed=executed,
                        observations=observations,
                        success=False,
                        error="shell command blocked: path outside approved roots",
                    )
                if policy.requires_confirm(spec, step_args):
                    if confirm is None:
                        return SkillReplayResult(
                            skill_id=skill_id,
                            goal=skill.goal_pattern,
                            steps_executed=executed,
                            observations=observations,
                            success=False,
                            error=f"confirmation required for {tool}",
                        )
                    desc = registry.describe_call(tool, step_args)
                    approved = await confirm(tool, desc, step_args)
                    if not approved:
                        return SkillReplayResult(
                            skill_id=skill_id,
                            goal=skill.goal_pattern,
                            steps_executed=executed,
                            observations=observations,
                            success=False,
                            error=f"user declined {tool}",
                        )
            try:
                obs = dispatch(tool, step_args, ctx)
                observations.append(str(obs))
                executed += 1
                if str(obs).startswith("ERROR"):
                    return SkillReplayResult(
                        skill_id=skill_id,
                        goal=skill.goal_pattern,
                        steps_executed=executed,
                        observations=observations,
                        success=False,
                        error=str(obs),
                    )
            except Exception as exc:  # noqa: BLE001
                return SkillReplayResult(
                    skill_id=skill_id,
                    goal=skill.goal_pattern,
                    steps_executed=executed,
                    observations=observations,
                    success=False,
                    error=str(exc),
                )

        return SkillReplayResult(
            skill_id=skill_id,
            goal=skill.goal_pattern,
            steps_executed=executed,
            observations=observations,
            success=True,
        )

    def prompt_slice(self, goal: str, limit: int = 2) -> str:
        skills = self.retrieve(goal, limit=limit)
        if not skills:
            return ""
        lines = [
            "Reusable learned skills (prefer when goal matches; substitute {{param}} placeholders):"
        ]
        for sk in skills:
            lines.append(f"- **{sk.name}** (id={sk.id}, used {sk.success_count}x): {sk.description}")
            if sk.parameters:
                lines.append(f"  Parameters: {', '.join(sk.parameters)}")
            for i, step in enumerate(sk.steps[:6], 1):
                tool = step.get("tool", "?")
                step_args = step.get("args", {})
                lines.append(f"  {i}. {tool}({json.dumps(step_args, ensure_ascii=False)[:80]})")
        return "\n".join(lines)

    def _row_to_skill(self, row: sqlite3.Row, score: float = 0.0) -> Skill:
        return Skill(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            goal_pattern=row["goal_pattern"],
            parameters=json.loads(row["parameters"] or "[]"),
            steps=json.loads(row["steps"] or "[]"),
            success_count=int(row["success_count"]),
            score=score,
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )

    def close(self) -> None:
        self._conn.close()


def _apply_param_substitution(value: str, args: dict[str, Any]) -> str:
    result = value
    for key, val in args.items():
        result = result.replace(f"{{{{{key}}}}}", str(val))
    for i in range(1, 9):
        placeholder = f"{{{{param{i}}}}}"
        if placeholder in result:
            subst = args.get(f"param{i}") or args.get(str(i))
            if subst is not None:
                result = result.replace(placeholder, str(subst))
    return result


def _extract_parameters(goal: str, trace: list[dict[str, Any]]) -> list[str]:
    params: list[str] = []
    for m in re.finditer(r'"([^"]+)"|\'([^\']+)\'', goal):
        val = m.group(1) or m.group(2)
        if val and val not in params:
            params.append(val)
    for step in trace:
        for key, val in (step.get("args") or {}).items():
            if isinstance(val, str) and 2 < len(val) < 80 and val not in params:
                if key in ("name", "url", "path", "text", "to", "command"):
                    params.append(val)
    return params[:8]


def _parameterize_steps(
    trace: list[dict[str, Any]],
    params: list[str],
) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for step in trace:
        tool = step.get("tool", "")
        if tool in ("finish", "get_screen_context"):
            continue
        args = dict(step.get("args") or {})
        for i, param in enumerate(params):
            placeholder = f"{{{{param{i+1}}}}}"
            for key, val in list(args.items()):
                if isinstance(val, str) and param in val:
                    args[key] = val.replace(param, placeholder)
        steps.append({"tool": tool, "args": args})
    return steps


def _skill_name_from_goal(goal: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", goal.lower().strip())[:48].strip("_")
    return slug or "learned_skill"
