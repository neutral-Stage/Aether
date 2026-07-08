"""Formal tool registry (§12.1) with ToolSpec + dispatch."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ..perception import accessibility as ax
from ..perception import screen as screen_cap
from ..effectors import input as kbd
from ..effectors import apps as app_ctl
from ..effectors import shell as shell_fx
from ..effectors import ax_actions
from ..effectors import applescript as ascript
from ..effectors import browser as browser_fx
from ..effectors import executor
from ..tools import delegation as delegate_fx
from ..core import stop as stop_ctl

Handler = Callable[[dict, "AgentContext"], str]


@dataclass
class AgentContext:
    """Mutable per-run context shared by tool handlers."""
    careful: bool = False
    elements: list = field(default_factory=list)
    last_screenshot: str | None = None
    world: Any = None  # WorldModel, optional
    memory: Any = None  # MemoryStore, optional
    browser_headless: bool = True
    browser_attach_mode: str = "headless"  # headless | cdp
    browser_cdp_url: str = "http://127.0.0.1:9222"


@dataclass
class ToolSpec:
    name: str
    json_schema: dict
    permission: str
    impact: str  # read | reversible | destructive
    description: str = ""
    handler: Handler | None = None

    def to_anthropic(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.json_schema,
        }


class Registry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def register_dynamic(
        self,
        *,
        name: str,
        description: str,
        json_schema: dict,
        permission: str,
        impact: str,
        handler: Handler,
    ) -> None:
        self.register(ToolSpec(
            name=name,
            description=description,
            json_schema=json_schema,
            permission=permission,
            impact=impact,
            handler=handler,
        ))

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def all_specs(self) -> list[ToolSpec]:
        return list(self._tools.values())

    def schemas(self) -> list[dict]:
        return [t.to_anthropic() for t in self._tools.values()]

    def describe_call(self, name: str, args: dict) -> str:
        if name == "click" and "element_index" in args:
            suffix = f" in {args['app']}" if args.get("app") else ""
            return f"click element [{args['element_index']}]{suffix}"
        if name == "get_app_context":
            return f"read UI of {args.get('app', '')}"
        if name == "watch_app":
            return f"watch {args.get('app', '')} for changes"
        if name == "unwatch_app":
            return f"stop watching {args.get('app', '')}"
        if name == "list_windows":
            return "list app windows"
        if name == "open_app":
            return f"open {args.get('name')}"
        if name == "type_text":
            # Never echo the literal text — it can be a password/secret and this
            # description is stored in the trace + learned recipes (Phase 10).
            t = args.get("text", "")
            return f"type {len(t)} chars"
        if name == "press_key":
            mods = "+".join(args.get("modifiers", []))
            return f"press {mods + '+' if mods else ''}{args.get('key')}"
        if name == "run_shell":
            return f"run `{args.get('command', '')[:60]}`"
        if name == "run_applescript":
            return "run AppleScript"
        if name == "finder_go_to":
            return f"Finder → {args.get('path', '')[:50]}"
        if name == "safari_open_url":
            return f"Safari → {args.get('url', '')[:50]}"
        if name == "mail_compose":
            return f"compose mail to {args.get('to', '(draft)')}"
        if name == "analyze_screen":
            return "analyze screen (OCR/vision)"
        if name == "remember_fact":
            return f"remember: {args.get('text', '')[:40]}"
        if name == "browser_navigate":
            return f"browser → {args.get('url', '')[:50]}"
        if name == "browser_click":
            return f"browser click {args.get('selector', '')[:40]}"
        if name == "browser_fill":
            return f"browser fill {args.get('selector', '')[:40]}"
        if name == "browser_get_text":
            return f"browser read {args.get('selector', '')[:40]}"
        if name == "browser_screenshot":
            return "browser screenshot"
        if name == "delegate_to_coder":
            return f"delegate to {args.get('agent', 'auto')} coder"
        if name == "spawn_agent":
            p = args.get("prompt", "")
            return f"spawn {args.get('agent_type')} agent: \"{p[:50]}{'…' if len(p) > 50 else ''}\""
        if name == "send_to_agent":
            return f"send to agent [{args.get('session_id', '')[:12]}]"
        if name == "get_agent_output":
            return f"read agent output [{args.get('session_id', '')[:12]}]"
        if name == "list_agents":
            return "list agent sessions"
        if name == "stop_agent":
            return f"stop agent [{args.get('session_id', '')[:12]}]"
        if name == "wait_for_agent":
            return f"wait for agent [{args.get('session_id', '')[:12]}]"
        if name.startswith("mcp_"):
            return name.replace("_", " ", 1)
        return name

    def dispatch(self, name: str, args: dict, ctx: AgentContext) -> str:
        stop_ctl.check()
        spec = self._tools.get(name)
        if spec is None:
            return f"Unknown tool: {name}"
        if spec.handler is None:
            return f"Tool {name} has no handler."
        try:
            return spec.handler(args, ctx)
        except stop_ctl.StopRequested:
            raise
        except Exception as e:  # noqa: BLE001
            return f"ERROR running {name}: {e}"


# ---- handlers ----

def _h_get_screen_context(_args: dict, ctx: AgentContext) -> str:
    data = ax.screen_context(capture_handles=True)
    ctx.elements = data["elements"]
    if ctx.world is not None:
        ctx.world.elements = data["elements"]
        ctx.world.frontmost_app = data.get("frontmost_app", "")
        ctx.world.ax_rendered = data.get("rendered", "")
        ctx.world.element_count = int(data.get("element_count", 0))
    return (f"Frontmost app: {data['frontmost_app']} "
            f"({data['element_count']} elements)\n{data['rendered']}")


def _h_open_app(args: dict, _ctx: AgentContext) -> str:
    res = app_ctl.open_app(args["name"])
    time.sleep(1.0)
    return res


def _resolve_click_target(args: dict, ctx: AgentContext) -> tuple[float, float]:
    if "element_index" in args and args["element_index"] is not None:
        idx = int(args["element_index"])
        for el in ctx.elements:
            if el.get("idx") == idx:
                cx = el["x"] + el["w"] / 2.0
                cy = el["y"] + el["h"] / 2.0
                return (cx, cy)
        raise ValueError(f"element_index {idx} not found — call get_screen_context again.")
    if "x" in args and "y" in args:
        return (float(args["x"]), float(args["y"]))
    raise ValueError("click needs element_index or (x, y).")


def _try_native_effector(tool: str, args: dict) -> str | None:
    """Optional Swift effector path when beta.native_effectors is enabled."""
    try:
        from ..core.config import load_config
        if not bool(load_config().get("beta", "native_effectors", default=False)):
            return None
        from ..ipc.native_effector import available, invoke_native
        if not available():
            return None
        return invoke_native(tool, args)
    except Exception:
        return None


def _h_click(args: dict, ctx: AgentContext) -> str:
    app = (args.get("app") or "").strip()
    if app:
        return _background_click(app, args)
    native = _try_native_effector("click", args)
    if native:
        time.sleep(0.2)
        return native
    idx = args.get("element_index")
    if idx is not None and ax_actions.can_press(int(idx)) and not args.get("double"):
        if args.get("button", "left") == "left":
            return ax_actions.press_element(int(idx))
    x, y = _resolve_click_target(args, ctx)
    count = 2 if args.get("double") else 1
    with executor.HID_LOCK:
        kbd.click(x, y, button=args.get("button", "left"), count=count)
    time.sleep(0.3)
    return f"Clicked at ({int(x)}, {int(y)})."


def _background_click(app: str, args: dict) -> str:
    """AX-first background click; guarded focus-juggle fallback for x,y."""
    idx = args.get("element_index")
    if idx is not None:
        handle = ax_actions.app_handle(app, int(idx))
        if handle is None:
            return (f"ERROR: no element [{idx}] cached for '{app}' — "
                    f"call get_app_context first.")
        try:
            return ax_actions.press_handle(handle, label=f"[{idx}] in {app}")
        except Exception as e:  # noqa: BLE001 — fall through to focus-juggle
            fallback_reason = str(e)
    else:
        fallback_reason = "coordinate click needs focus"
    info = ax.resolve_app(app)
    if info is None:
        return f"ERROR: app not running: {app}"
    if "x" not in args or "y" not in args:
        return (f"ERROR: background AXPress failed ({fallback_reason}) and no x,y "
                f"for focus-switch fallback.")
    x, y = float(args["x"]), float(args["y"])
    try:
        executor.focused_action(
            info["pid"],
            lambda: kbd.click(x, y, button=args.get("button", "left"),
                              count=2 if args.get("double") else 1),
        )
        return f"Clicked ({int(x)},{int(y)}) in {app} via brief focus switch."
    except RuntimeError as e:
        return f"ERROR: {e}"


def _h_type_text(args: dict, _ctx: AgentContext) -> str:
    app = (args.get("app") or "").strip()
    if app:
        idx = args.get("element_index")
        if idx is None:
            return "ERROR: typing into a background app needs element_index (AXSetValue)."
        handle = ax_actions.app_handle(app, int(idx))
        if handle is None:
            return (f"ERROR: no element [{idx}] cached for '{app}' — "
                    f"call get_app_context first.")
        return ax_actions.set_value_handle(
            handle, args["text"], label=f"[{idx}] in {app}")
    native = _try_native_effector("type_text", args)
    if native:
        return native
    with executor.HID_LOCK:
        kbd.type_text(args["text"])
    return f"Typed {len(args['text'])} characters."


def _h_press_key(args: dict, _ctx: AgentContext) -> str:
    with executor.HID_LOCK:
        kbd.press_key(args["key"], modifiers=args.get("modifiers"))
    time.sleep(0.2)
    return f"Pressed {args['key']}."


def _h_get_app_context(args: dict, _ctx: AgentContext) -> str:
    data = ax.app_context(args["app"])
    if data.get("error"):
        return f"ERROR: {data['error']}"
    state = "frontmost" if data.get("active") else "background"
    return (f"App: {data['app']} ({state}, {data['element_count']} elements)\n"
            f"{data['rendered']}")


def _h_watch_app(args: dict, _ctx: AgentContext) -> str:
    from ..perception.app_watcher import AppWatcher
    watcher = AppWatcher.get()
    then_goal = (args.get("then_goal") or "").strip()
    if then_goal:
        return watcher.add_trigger(
            args["app"], contains=(args.get("when") or "").strip(),
            goal=then_goal, auto=bool(args.get("auto", False)),
        )
    return watcher.watch(args["app"])


def _h_unwatch_app(args: dict, _ctx: AgentContext) -> str:
    from ..perception.app_watcher import AppWatcher
    return AppWatcher.get().unwatch(args["app"])


def _h_list_windows(_args: dict, _ctx: AgentContext) -> str:
    rows = ax.list_windows()
    if not rows:
        return "No running apps visible (or Accessibility unavailable)."
    lines = []
    for r in rows[:20]:
        wins = "; ".join(r["windows"][:3]) or "(no windows)"
        lines.append(f"{r['app']}: {wins}")
    return "\n".join(lines)


def _h_run_shell(args: dict, _ctx: AgentContext) -> str:
    return shell_fx.run(args["command"]).summary()


def _h_screenshot(_args: dict, ctx: AgentContext) -> str:
    path = screen_cap.capture_to_file()
    ctx.last_screenshot = path
    if ctx.world is not None:
        ctx.world.last_screenshot = path
    return f"Saved screenshot to {path}. Use analyze_screen for OCR/vision."


def _h_analyze_screen(args: dict, ctx: AgentContext) -> str:
    from ..perception import ocr
    from ..perception import vision as vision_mod

    path = args.get("path") or ctx.last_screenshot
    if not path:
        path = screen_cap.capture_to_file()
    ctx.last_screenshot = path
    if ctx.world is not None:
        ctx.world.last_screenshot = path

    mode = args.get("mode", "ocr")  # ocr | full
    ocr_text = ocr.recognize_text_formatted(path)
    parts = [f"Screenshot: {path}", ocr_text]

    if mode == "full" and not ocr.available():
        parts.append("(Vision framework unavailable — OCR-only.)")

    if mode == "full":
        result = vision_mod.analyze_screen(path, use_cloud=False)
        if result.get("region_count", 0) == 0:
            parts.append("AX may be insufficient — few OCR regions found.")

    return "\n".join(parts)


def _h_remember_fact(args: dict, ctx: AgentContext) -> str:
    text = args.get("text", "").strip()
    if not text:
        return "ERROR: text is required."
    if ctx.memory is None:
        return "Long-term memory is disabled in config."
    kind = args.get("kind", "fact")
    row_id = ctx.memory.remember(text, kind=kind)
    return f"Remembered ({kind}) id={row_id}: {text[:80]}"


def _browser_session(ctx: AgentContext):
    return browser_fx.get_session(
        headless=ctx.browser_headless,
        attach_mode=ctx.browser_attach_mode,
        cdp_url=ctx.browser_cdp_url,
    )


def _h_browser_navigate(args: dict, ctx: AgentContext) -> str:
    if not _browser_session(ctx).available():
        return (
            "Playwright not installed. Run: pip install playwright && "
            "playwright install chromium"
        )
    return browser_fx.navigate(args["url"], headless=ctx.browser_headless,
                               tab_id=args.get("tab_id"))


def _h_browser_click(args: dict, ctx: AgentContext) -> str:
    if not _browser_session(ctx).available():
        return "Playwright not installed."
    return browser_fx.click(args["selector"], tab_id=args.get("tab_id"))


def _h_browser_fill(args: dict, ctx: AgentContext) -> str:
    if not _browser_session(ctx).available():
        return "Playwright not installed."
    return browser_fx.fill(args["selector"], args["text"], tab_id=args.get("tab_id"))


def _h_browser_get_text(args: dict, ctx: AgentContext) -> str:
    if not _browser_session(ctx).available():
        return "Playwright not installed."
    return browser_fx.get_text(args["selector"], tab_id=args.get("tab_id"))


def _h_browser_tabs(_args: dict, ctx: AgentContext) -> str:
    if not _browser_session(ctx).available():
        return "Playwright not installed."
    return browser_fx.list_tabs()


def _h_browser_activate_tab(args: dict, ctx: AgentContext) -> str:
    if not _browser_session(ctx).available():
        return "Playwright not installed."
    return browser_fx.activate_tab(int(args["tab_id"]))


def _h_browser_new_tab(args: dict, ctx: AgentContext) -> str:
    if not _browser_session(ctx).available():
        return "Playwright not installed."
    return browser_fx.new_tab(args.get("url"))


def _h_browser_attach(_args: dict, ctx: AgentContext) -> str:
    from ..effectors import chrome_launcher
    result = chrome_launcher.launch_with_debug_port()
    # Switch this run's session to CDP mode so subsequent browser_* attach.
    ctx.browser_attach_mode = "cdp"
    browser_fx.close_session()
    return result


def _h_browser_screenshot(_args: dict, ctx: AgentContext) -> str:
    if not _browser_session(ctx).available():  # prime singleton in the right attach mode
        return "Playwright not installed."
    path = browser_fx.screenshot()
    return path


def _h_computer_use_step(args: dict, _ctx: AgentContext) -> str:
    from ..core.config import load_config
    from ..effectors import computer_use
    key = load_config(validate=False).anthropic_api_key
    if not key:
        return "ERROR: computer_use_step needs ANTHROPIC_API_KEY."
    try:
        return computer_use.computer_use_step(args["instruction"], api_key=key)
    except Exception as e:  # noqa: BLE001
        return f"ERROR: computer-use failed: {e}"


def _h_finish(args: dict, _ctx: AgentContext) -> str:
    return args.get("message", "Done.")


def _h_run_applescript(args: dict, _ctx: AgentContext) -> str:
    return ascript.run_applescript(args["source"]).summary()


def _h_finder_go_to(args: dict, _ctx: AgentContext) -> str:
    return ascript.finder_go_to(args["path"])


def _h_safari_open_url(args: dict, _ctx: AgentContext) -> str:
    return ascript.safari_open_url(args["url"])


def _h_mail_compose(args: dict, _ctx: AgentContext) -> str:
    return ascript.mail_compose(
        to=args.get("to", ""),
        subject=args.get("subject", ""),
        body=args.get("body", ""),
    )


def _h_delegate_to_coder(args: dict, _ctx: AgentContext) -> str:
    from ..core.config import load_config

    cfg = load_config(validate=False)
    deleg = cfg.get("delegation") or {}
    if not bool(deleg.get("enabled", True)):
        return "delegate_to_coder is disabled (set delegation.enabled: true in config.yaml)."
    policy = cfg.get("policy") or {}
    return delegate_fx.delegate_to_coder(
        prompt=args["prompt"],
        agent=args.get("agent", deleg.get("default_agent", "auto")),
        workspace=args.get("workspace"),
        timeout_sec=int(args.get("timeout_sec", deleg.get("timeout_sec", 300))),
        structured=bool(deleg.get("structured_output", True)),
        env_allowlist=deleg.get("env_allowlist"),
        approved_roots=policy.get("approved_file_roots"),
    )


def build_default_registry() -> Registry:
    reg = Registry()
    specs = [
        ToolSpec(
            name="get_screen_context",
            description=("Read what's currently on screen: the frontmost app and a "
                         "numbered list of UI elements. Call FIRST and after actions "
                         "that change the screen."),
            json_schema={"type": "object", "properties": {}},
            permission="screen",
            impact="read",
            handler=_h_get_screen_context,
        ),
        ToolSpec(
            name="open_app",
            description="Launch or focus a macOS app by name, e.g. 'Safari', 'Mail', 'Finder'.",
            json_schema={
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
            permission="input",
            impact="reversible",
            handler=_h_open_app,
        ),
        ToolSpec(
            name="click",
            description=("Click a UI element. Prefer element_index from get_screen_context; "
                         "uses AXPress when available, else CGEvent. Or pass x,y coordinates. "
                         "Pass app='Name' to click in a BACKGROUND app (element_index from "
                         "get_app_context; AX-first, no focus steal)."),
            json_schema={
                "type": "object",
                "properties": {
                    "element_index": {"type": "integer"},
                    "x": {"type": "number"},
                    "y": {"type": "number"},
                    "button": {"type": "string", "enum": ["left", "right"]},
                    "double": {"type": "boolean"},
                    "app": {"type": "string"},
                },
            },
            permission="input",
            impact="reversible",
            handler=_h_click,
        ),
        ToolSpec(
            name="type_text",
            description=("Type text into the currently focused field. For a BACKGROUND app "
                         "pass app='Name' + element_index (sets the field value via AX "
                         "without stealing focus)."),
            json_schema={
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "app": {"type": "string"},
                    "element_index": {"type": "integer"},
                },
                "required": ["text"],
            },
            permission="input",
            impact="reversible",
            handler=_h_type_text,
        ),
        ToolSpec(
            name="get_app_context",
            description=("Read the UI elements of ANY running app by name — foreground or "
                         "background — without changing focus. Use before background click/"
                         "type_text with app=."),
            json_schema={
                "type": "object",
                "properties": {"app": {"type": "string"}},
                "required": ["app"],
            },
            permission="screen",
            impact="read",
            handler=_h_get_app_context,
        ),
        ToolSpec(
            name="watch_app",
            description=("Watch a background app for changes. With then_goal, set a "
                         "PROACTIVE trigger: when the app changes (optionally matching "
                         "`when` text) Aether suggests (or auto-runs) that goal. "
                         "E.g. watch Xcode when='Build Succeeded' then_goal='run the tests'."),
            json_schema={
                "type": "object",
                "properties": {
                    "app": {"type": "string"},
                    "when": {"type": "string", "description": "Substring the change must contain"},
                    "then_goal": {"type": "string", "description": "Goal to suggest/run on match"},
                    "auto": {"type": "boolean", "description": "Auto-run vs suggest (default suggest)"},
                },
                "required": ["app"],
            },
            permission="screen",
            impact="read",
            handler=_h_watch_app,
        ),
        ToolSpec(
            name="unwatch_app",
            description="Stop watching a background app.",
            json_schema={
                "type": "object",
                "properties": {"app": {"type": "string"}},
                "required": ["app"],
            },
            permission="screen",
            impact="read",
            handler=_h_unwatch_app,
        ),
        ToolSpec(
            name="list_windows",
            description="List running apps and their window titles (all apps, not just frontmost).",
            json_schema={"type": "object", "properties": {}},
            permission="screen",
            impact="read",
            handler=_h_list_windows,
        ),
        ToolSpec(
            name="press_key",
            description=("Press a key with optional modifiers, e.g. key='return', or "
                         "key='c' modifiers=['cmd']."),
            json_schema={
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "modifiers": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["key"],
            },
            permission="input",
            impact="reversible",
            handler=_h_press_key,
        ),
        ToolSpec(
            name="run_shell",
            description="Run a shell command and return its output.",
            json_schema={
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
            permission="shell",
            impact="reversible",
            handler=_h_run_shell,
        ),
        ToolSpec(
            name="screenshot",
            description="Capture the screen to a PNG file (returns the path).",
            json_schema={"type": "object", "properties": {}},
            permission="screen",
            impact="read",
            handler=_h_screenshot,
        ),
        ToolSpec(
            name="analyze_screen",
            description=("OCR/vision analysis when AX is insufficient. "
                         "mode='ocr' (default) or 'full'."),
            json_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "PNG path (optional)"},
                    "mode": {"type": "string", "enum": ["ocr", "full"]},
                },
            },
            permission="screen",
            impact="read",
            handler=_h_analyze_screen,
        ),
        ToolSpec(
            name="remember_fact",
            description="Store a fact in long-term memory for future tasks.",
            json_schema={
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "kind": {"type": "string", "description": "fact|preference|quirk"},
                },
                "required": ["text"],
            },
            permission="none",
            impact="reversible",
            handler=_h_remember_fact,
        ),
        ToolSpec(
            name="browser_navigate",
            description=("Open a URL in the browser (headless Chromium, or the user's "
                         "real Chrome when attached). Optional tab_id targets a tab "
                         "from browser_tabs."),
            json_schema={
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "tab_id": {"type": "integer"},
                },
                "required": ["url"],
            },
            permission="network",
            impact="reversible",
            handler=_h_browser_navigate,
        ),
        ToolSpec(
            name="browser_click",
            description="Click an element in the browser by CSS selector (optional tab_id).",
            json_schema={
                "type": "object",
                "properties": {
                    "selector": {"type": "string"},
                    "tab_id": {"type": "integer"},
                },
                "required": ["selector"],
            },
            permission="network",
            impact="reversible",
            handler=_h_browser_click,
        ),
        ToolSpec(
            name="browser_fill",
            description="Fill a form field in the browser (optional tab_id).",
            json_schema={
                "type": "object",
                "properties": {
                    "selector": {"type": "string"},
                    "text": {"type": "string"},
                    "tab_id": {"type": "integer"},
                },
                "required": ["selector", "text"],
            },
            permission="network",
            impact="reversible",
            handler=_h_browser_fill,
        ),
        ToolSpec(
            name="browser_get_text",
            description="Read text from an element in the browser (optional tab_id).",
            json_schema={
                "type": "object",
                "properties": {
                    "selector": {"type": "string"},
                    "tab_id": {"type": "integer"},
                },
                "required": ["selector"],
            },
            permission="network",
            impact="read",
            handler=_h_browser_get_text,
        ),
        ToolSpec(
            name="browser_screenshot",
            description="Screenshot the browser viewport.",
            json_schema={"type": "object", "properties": {}},
            permission="screen",
            impact="read",
            handler=_h_browser_screenshot,
        ),
        ToolSpec(
            name="browser_tabs",
            description=("List open browser tabs with ids, titles, urls. In attached "
                         "mode these are the user's real Chrome tabs."),
            json_schema={"type": "object", "properties": {}},
            permission="network",
            impact="read",
            handler=_h_browser_tabs,
        ),
        ToolSpec(
            name="browser_activate_tab",
            description="Switch the active browser tab by tab_id (from browser_tabs).",
            json_schema={
                "type": "object",
                "properties": {"tab_id": {"type": "integer"}},
                "required": ["tab_id"],
            },
            permission="network",
            impact="reversible",
            handler=_h_browser_activate_tab,
        ),
        ToolSpec(
            name="browser_new_tab",
            description="Open a new browser tab, optionally at a URL.",
            json_schema={
                "type": "object",
                "properties": {"url": {"type": "string"}},
            },
            permission="network",
            impact="reversible",
            handler=_h_browser_new_tab,
        ),
        ToolSpec(
            name="browser_attach",
            description=("Attach to the user's REAL Chrome: quits and relaunches Chrome "
                         "with remote debugging (tabs restore), then browser_* tools "
                         "control actual tabs. Ask the user before using this."),
            json_schema={"type": "object", "properties": {}},
            permission="network",
            impact="destructive",
            handler=_h_browser_attach,
        ),
        ToolSpec(
            name="run_applescript",
            description="Run arbitrary AppleScript and return output.",
            json_schema={
                "type": "object",
                "properties": {"source": {"type": "string"}},
                "required": ["source"],
            },
            permission="input",
            impact="reversible",
            handler=_h_run_applescript,
        ),
        ToolSpec(
            name="finder_go_to",
            description="Navigate Finder front window to a POSIX path (e.g. /Users/you/Downloads).",
            json_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            permission="input",
            impact="reversible",
            handler=_h_finder_go_to,
        ),
        ToolSpec(
            name="safari_open_url",
            description="Open a URL in Safari (activates Safari, sets front tab URL).",
            json_schema={
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
            permission="input",
            impact="reversible",
            handler=_h_safari_open_url,
        ),
        ToolSpec(
            name="mail_compose",
            description=("Open Mail with a new compose window (visible, not sent). "
                         "Use for drafting; sending requires separate confirmation."),
            json_schema={
                "type": "object",
                "properties": {
                    "to": {"type": "string"},
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                },
            },
            permission="input",
            impact="reversible",
            handler=_h_mail_compose,
        ),
        ToolSpec(
            name="finish",
            description="Call when the task is complete. `message` is spoken to the user.",
            json_schema={
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
            },
            permission="none",
            impact="read",
            handler=_h_finish,
        ),
    ]
    for s in specs:
        reg.register(s)
    from ..core.config import load_config

    cfg = load_config(validate=False)
    if bool((cfg.get("delegation") or {}).get("enabled", True)):
        reg.register(
            ToolSpec(
                name="delegate_to_coder",
                description=(
                    "Tier-0: delegate a coding task to a specialist CLI agent "
                    "(claude, codex, opencode, cursor) and wait for it to finish. "
                    "For long-running or PARALLEL work, prefer spawn_agent (fleet) "
                    "which runs in the background and is steerable."
                ),
                json_schema={
                    "type": "object",
                    "properties": {
                        "prompt": {"type": "string", "description": "Detailed task spec"},
                        "agent": {
                            "type": "string",
                            "enum": ["auto", "claude", "codex", "opencode", "cursor"],
                            "description": "Which coding CLI to use (auto picks first available)",
                        },
                        "workspace": {
                            "type": "string",
                            "description": "Project directory (default: cwd)",
                        },
                        "timeout_sec": {
                            "type": "integer",
                            "description": "Max seconds to wait (default 300)",
                        },
                    },
                    "required": ["prompt"],
                },
                permission="shell",
                impact="reversible",
                handler=_h_delegate_to_coder,
            )
        )
    fleet_cfg = cfg.get("fleet") or {}
    if bool(fleet_cfg.get("enabled", True)):
        from ..fleet.manager import SessionManager
        from ..fleet.tools import register_fleet_tools

        policy_cfg = cfg.get("policy") or {}
        SessionManager.get().configure(
            fleet_cfg,
            approved_roots=policy_cfg.get("approved_file_roots"),
            mcp_cfg=cfg.get("mcp_server") or {},
        )
        register_fleet_tools(reg)
        # Bridge global STOP → kill fleet subprocesses + cancel graphs (Phase 9).
        from ..fleet.manager import register_stop_bridge
        register_stop_bridge()
    if bool((cfg.get("beta") or {}).get("computer_use_api", False)):
        reg.register(ToolSpec(
            name="computer_use_step",
            description=("LAST RESORT when AX + OCR both fail: send a screenshot to "
                         "Anthropic's computer-use model and execute the ONE action it "
                         "proposes. Slower and costs tokens — prefer AX/vision tools."),
            json_schema={
                "type": "object",
                "properties": {"instruction": {"type": "string"}},
                "required": ["instruction"],
            },
            permission="input",
            impact="reversible",
            handler=_h_computer_use_step,
        ))
    return reg


# Module-level default registry (singleton)
DEFAULT_REGISTRY = build_default_registry()
