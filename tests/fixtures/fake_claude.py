#!/usr/bin/env python3
"""Fake claude CLI emitting canned stream-json — fleet tests need no API keys.

Ignores argv (accepts any flags). Reads NDJSON user messages on stdin, emits
system init, assistant text + tool_use, then a cumulative-cost result per turn.
"""
import json
import sys


def emit(obj):
    print(json.dumps(obj), flush=True)


def main():
    emit({"type": "system", "subtype": "init", "session_id": "fake-123"})
    turn = 0
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("type") != "user":
            continue
        turn += 1
        content = (msg.get("message") or {}).get("content") or []
        text = " ".join(
            b.get("text", "") for b in content if isinstance(b, dict)
        )
        emit({
            "type": "assistant",
            "message": {"content": [
                {"type": "text", "text": "working on: " + text[:40]},
            ]},
        })
        emit({
            "type": "assistant",
            "message": {"content": [
                {"type": "tool_use", "name": "Bash", "input": {"command": "echo hi"}},
            ]},
        })
        emit({
            "type": "result",
            "subtype": "success",
            "total_cost_usd": 0.01 * turn,  # cumulative across turns
            "result": "turn %d done" % turn,
            "session_id": "fake-123",
        })
    sys.exit(0)


if __name__ == "__main__":
    main()
