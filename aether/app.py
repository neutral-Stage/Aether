"""Aether Phase 2 entrypoint / CLI.

Examples:
  python -m aether.app --text "Open Safari and go to apple.com"
  python -m aether.app                # interactive text REPL
  python -m aether.app --voice        # push-to-talk voice loop
  python -m aether.app --careful ...  # confirm before every action
  python -m aether.app --no-hud ...   # disable overlay HUD
"""
from __future__ import annotations

import argparse
import sys

from .core.config import load_config
from .core import permissions
from .core import stop as stop_ctl
from .core.orchestrator import Agent
from .hud.overlay import HUD
from .voice.stt import STT

QUIT = {"quit", "exit", "bye", ""}


def _preflight() -> None:
    st = permissions.status(prompt=True)
    if not st.accessibility:
        print("\n⚠️  Accessibility permission is MISSING — Aether cannot control the "
              "Mac yet.\n   System Settings → Privacy & Security → Accessibility → "
              "enable your terminal/app, then restart it.\n")
    if st.screen_recording is False:
        print("ℹ️  Screen Recording is off (only needed for screenshots).\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aether", description="Aether Phase 2 agent")
    parser.add_argument("--text", help="run a single command and exit")
    parser.add_argument("--voice", action="store_true", help="voice (push-to-talk) loop")
    parser.add_argument("--careful", action="store_true",
                        help="confirm before every action")
    parser.add_argument("--no-hud", action="store_true", help="disable the overlay HUD")
    parser.add_argument("--local-only", action="store_true",
                        help="prefer local model (Ollama); cloud fallback if needed")
    parser.add_argument("--no-preflight", action="store_true",
                        help="skip the permission check")
    parser.add_argument("--doctor", action="store_true",
                        help="run the first-run environment preflight and exit")
    args = parser.parse_args(argv)

    if args.doctor:
        from .core.doctor import main as doctor_main
        return doctor_main()

    cfg = load_config()
    if args.careful:
        cfg.raw.setdefault("agent", {})["careful"] = True
    if args.no_hud:
        cfg.raw.setdefault("hud", {})["enabled"] = False
    if args.local_only:
        cfg.raw.setdefault("agent", {})["local_only"] = True

    if not cfg.has_cloud_llm() and not cfg.local_only:
        print("ERROR: No cloud LLM API key set. Copy .env.example to .env and add a provider key.")
        print("       Or use --local-only with Ollama running (see configs/router.yaml).")
        return 2

    if not args.no_preflight:
        _preflight()

    hud = HUD(enabled=cfg.hud_enabled)
    hud.start()

    stop_ctl.start_hotkey_monitor(
        hotkey=cfg.stop_hotkey,
        escape_double_tap=cfg.stop_escape_double_tap,
    )

    agent = Agent(cfg, hud=hud)

    # One-shot
    if args.text:
        agent.run(args.text)
        return 0

    # Voice loop
    if args.voice:
        stt = STT(engine=cfg.stt, model=cfg.stt_model,
                  openai_api_key=cfg.openai_api_key,
                  record_seconds=cfg.record_seconds)
        print("Voice mode. Press Enter to start each capture; say 'stop' or 'quit' to exit.")
        while True:
            try:
                input("⏎ press Enter to speak…")
                goal = stt.listen()
            except (KeyboardInterrupt, EOFError):
                print("\nBye.")
                return 0
            if goal.strip().lower() in QUIT:
                print("Bye.")
                return 0
            if stop_ctl.is_stop_phrase(goal):
                stop_ctl.trigger("voice")
                print("Stopped.")
                continue
            agent.run(goal)

    # Interactive text REPL
    print("Aether (text mode). Type a command, or 'quit' to exit. "
          f"STOP: {cfg.stop_hotkey} or HUD button.")
    while True:
        try:
            goal = input("\nYou: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nBye.")
            return 0
        if goal.lower() in QUIT:
            print("Bye.")
            return 0
        if stop_ctl.is_stop_phrase(goal):
            stop_ctl.trigger("text")
            continue
        agent.run(goal)


if __name__ == "__main__":
    sys.exit(main())
