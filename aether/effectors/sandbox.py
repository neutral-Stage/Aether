"""macOS Seatbelt confinement for shell commands and coding agents.

Aether's policy gate governs only the agent's own process. Once a shell
command or a coding CLI is approved, the child process could still write
anywhere, read credentials, or plant persistence. This module wraps those
children in ``sandbox-exec`` with a profile generated per call:

- Writes are allowed only inside the approved roots, plus temp and cache
  folders (and, for coding agents, their own state folders).
- Some paths stay read-only even inside a root: shell startup files,
  LaunchAgents, ~/.ssh, git hooks and git config (both run code later), and
  Aether's own config, audit log and knowledge packs.
- Credential files are unreadable: SSH private keys, cloud CLI credentials,
  keychains, browser profiles, Aether's .env and audit key.
- Network is off unless the caller allows egress.
- No Apple Events, no LaunchServices opens, no signals to processes outside
  the sandbox: those would start or steer code that the sandbox doesn't cover.

The profile starts from ``(allow default)`` and denies what matters, the
pattern Bazel's macOS sandbox uses, so ordinary tools keep working. Later
rules win, so the order is: deny all writes, allow the roots, deny the
protected paths inside them. Paths are passed as ``-D`` parameters, never
spliced into the profile text, so a path cannot inject rules. The approach
follows Codex's Seatbelt integration (Apache-2.0); the profile is our own.

Seatbelt does not nest: a process that is already sandboxed cannot apply
another sandbox. Codex applies its own Seatbelt profile when it runs with
``--sandbox``, so Aether leaves those runs to Codex's sandbox.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

SANDBOX_EXEC = "/usr/bin/sandbox-exec"

# Home-relative paths that must never be written, even inside an approved root.
PROTECTED_WRITE = (
    ".zshrc", ".zshenv", ".zprofile", ".zlogin", ".zlogout",
    ".bashrc", ".bash_profile", ".bash_login", ".profile", ".inputrc",
    ".config/fish", ".ssh", ".gitconfig", ".config/git", ".aether",
    "Library/LaunchAgents", "Library/LaunchDaemons",
    "Library/Application Support/Aether",
    "Library/Application Support/com.apple.backgroundtaskmanagementagent",
)

# Home-relative credential stores whose contents are unreadable.
PROTECTED_READ = (
    ".ssh", ".aws", ".gnupg", ".config/gcloud", ".azure", ".kube",
    ".docker/config.json", ".netrc", ".git-credentials", ".password-store",
    ".config/gh/hosts.yml", ".config/op",
    "Library/Keychains", "Library/Cookies",
    "Library/Application Support/Google/Chrome",
    "Library/Application Support/BraveSoftware",
    "Library/Application Support/Microsoft Edge",
    "Library/Application Support/Arc",
    "Library/Application Support/Firefox/Profiles",
    "Library/Safari",
)

# Readable again inside the credential stores (public material ssh/git need).
READABLE_EXCEPTIONS = (r"\.pub$", r"/\.ssh/(known_hosts|config)$")

# Always-writable caches (package managers, compilers).
CACHE_DIRS = ("Library/Caches", ".cache", ".npm")

# Where coding CLIs keep their own state.
CODER_STATE_DIRS = (
    ".claude", ".claude.json", ".codex", ".cursor", ".config/opencode",
    ".local/share/opencode", ".local/state", ".kilocode",
)

# Anywhere: files that make git run code on the user's next git command.
_GIT_CODE_PATHS = (r"/\.git/hooks(/|$)", r"/\.git/config$")

_DEVICES_LITERAL = ("/dev/null", "/dev/zero", "/dev/tty", "/dev/stdout", "/dev/stderr",
                    "/dev/dtracehelper", "/dev/random", "/dev/urandom")
_DEVICES_REGEX = (r"^/dev/fd/", r"^/dev/ttys[0-9]+$", r"^/dev/ptmx$")


def available() -> bool:
    return sys.platform == "darwin" and os.path.exists(SANDBOX_EXEC)


def canon(path: str | os.PathLike[str]) -> str:
    """Absolute, symlink-resolved path (Seatbelt matches real paths: /tmp is /private/tmp)."""
    return os.path.realpath(os.path.expanduser(str(path)))


def _home(rel: str, home: str) -> str:
    return canon(os.path.join(home, rel))


def temp_dirs() -> list[str]:
    dirs = {canon("/tmp"), canon(tempfile.gettempdir())}
    t = canon(tempfile.gettempdir())
    if os.path.basename(t) == "T":           # /var/folders/xx/yyyy/T → also C (cache)
        dirs.add(os.path.dirname(t))
    return sorted(dirs)


def _sb_regex(pattern: str) -> str:
    return '#"' + pattern.replace('"', '\\"') + '"'


@dataclass(frozen=True)
class Profile:
    name: str
    write_roots: tuple[str, ...]
    protected_write: tuple[str, ...] = ()
    protected_read: tuple[str, ...] = ()
    network: bool = False
    readable_exceptions: tuple[str, ...] = field(default=READABLE_EXCEPTIONS)
    # Block the ways out of a file/network sandbox that macOS services offer:
    # Apple Events (osascript driving Terminal), LaunchServices (`open -a`
    # starts apps outside the sandbox) and signals to unsandboxed processes
    # (killing Aether or its STOP listener).
    confine_ipc: bool = True

    def render(self) -> tuple[str, dict[str, str]]:
        """The SBPL text and its -D parameters."""
        params: dict[str, str] = {}

        def param(prefix: str, value: str) -> str:
            key = f"{prefix}_{len([k for k in params if k.startswith(prefix + '_')])}"
            params[key] = value
            return f'(param "{key}")'

        lines = [f"; Aether sandbox: {self.name}", "(version 1)", "(allow default)",
                 "(deny file-write*)", "(allow file-write*"]
        lines += [f'    (literal "{d}")' for d in _DEVICES_LITERAL]
        lines += [f"    (regex {_sb_regex(r)})" for r in _DEVICES_REGEX]
        lines += [f"    (subpath {param('W', w)})" for w in self.write_roots]
        lines.append(")")
        if self.protected_write:
            lines.append("(deny file-write*")
            lines += [f"    (subpath {param('PW', p)})" for p in self.protected_write]
            lines.append(")")
        lines.append("(deny file-write*")
        lines += [f"    (regex {_sb_regex(r)})" for r in _GIT_CODE_PATHS]
        lines.append(")")
        if self.protected_read:
            lines.append("(deny file-read-data")
            lines += [f"    (subpath {param('PR', p)})" for p in self.protected_read]
            lines.append(")")
            if self.readable_exceptions:
                lines.append("(allow file-read-data")
                lines += [f"    (regex {_sb_regex(r)})" for r in self.readable_exceptions]
                lines.append(")")
        if not self.network:
            lines.append("(deny network*)")
        if self.confine_ipc:
            lines += ["(deny appleevent-send)", "(deny lsopen)",
                      "(deny signal)", "(allow signal (target same-sandbox))"]
        return "\n".join(lines) + "\n", params

    def wrap(self, argv: list[str]) -> list[str]:
        """argv run under this profile."""
        text, params = self.render()
        out = [SANDBOX_EXEC]
        for key, value in params.items():
            out += ["-D", f"{key}={value}"]
        return out + ["-p", text, *argv]

    def describe(self) -> str:
        net = "on" if self.network else "off"
        return (f"sandbox '{self.name}': writes only in {len(self.write_roots)} folder(s), "
                f"credentials unreadable, network {net}")


# ---- configuration --------------------------------------------------------------

def _settings() -> dict:
    try:
        from ..core.config import load_config

        cfg = load_config(validate=False)
        raw = cfg.raw or {}
    except Exception:  # noqa: BLE001 — sandbox defaults without config
        raw = {}
    s = dict(raw.get("sandbox") or {})
    s["_roots"] = list((raw.get("policy") or {}).get("approved_file_roots") or ["~"])
    s["_network_cap"] = bool((raw.get("capabilities") or {}).get("network", True))
    return s


def _self_paths() -> tuple[list[str], list[str]]:
    """Aether's own files: (never writable, never readable)."""
    from ..core.paths import ROOT, data_dir

    write = [canon(data_dir()), canon(ROOT / "config.yaml"), canon(ROOT / "configs"),
             canon(ROOT / ".env")]
    read = [canon(ROOT / ".env"), canon(data_dir() / ".audit_hmac_key")]
    cfg_env = os.getenv("AETHER_CONFIG_PATH", "").strip()
    if cfg_env:
        write.append(canon(cfg_env))
    return write, read


def _protected(settings: dict, home: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    pw = [_home(p, home) for p in PROTECTED_WRITE]
    pr = [_home(p, home) for p in PROTECTED_READ]
    pr += [canon(p) for p in settings.get("extra_deny_read") or []]
    if settings.get("protect_self", True):
        w, r = _self_paths()
        pw += w
        pr += r
    return tuple(dict.fromkeys(pw)), tuple(dict.fromkeys(pr))


def enabled(kind: str, settings: dict | None = None) -> bool:
    """Is confinement on for 'shell' or 'coders' (and can it run here)?"""
    s = settings if settings is not None else _settings()
    if not s.get("enabled", True):
        return False
    if not bool(s.get(kind, True)):
        return False
    return available()


def shell_profile(settings: dict | None = None, *, home: str | None = None) -> Profile:
    s = settings if settings is not None else _settings()
    home = home or str(Path.home())
    roots = [canon(r) for r in s.get("_roots") or ["~"] if r]
    roots += [canon(r) for r in s.get("extra_write_roots") or []]
    roots += [_home(c, home) for c in CACHE_DIRS] + temp_dirs()
    pw, pr = _protected(s, home)
    network = bool(s.get("shell_network", True)) and bool(s.get("_network_cap", True))
    return Profile("shell", tuple(dict.fromkeys(roots)), pw, pr, network)


def git_common_dir(workspace: str) -> str | None:
    """For a git worktree, the main repository's .git dir (commits write there)."""
    marker = Path(workspace) / ".git"
    if not marker.is_file():
        return None
    try:
        text = marker.read_text().strip()
    except OSError:
        return None
    if not text.startswith("gitdir:"):
        return None
    gitdir = Path(text.split(":", 1)[1].strip())
    if not gitdir.is_absolute():
        gitdir = (Path(workspace) / gitdir)
    # <repo>/.git/worktrees/<name> → <repo>/.git
    if gitdir.parent.name == "worktrees":
        return canon(gitdir.parent.parent)
    return canon(gitdir)


def coder_profile(workspace: str, settings: dict | None = None, *,
                  home: str | None = None) -> Profile:
    s = settings if settings is not None else _settings()
    home = home or str(Path.home())
    roots = [canon(workspace)]
    common = git_common_dir(workspace)
    if common:
        roots.append(common)
    roots += [canon(r) for r in s.get("extra_write_roots") or []]
    roots += [_home(c, home) for c in (*CACHE_DIRS, *CODER_STATE_DIRS)] + temp_dirs()
    pw, pr = _protected(s, home)
    network = bool(s.get("coder_network", True))
    return Profile("coder", tuple(dict.fromkeys(roots)), pw, pr, network)


def wrap_shell(command: str, *, settings: dict | None = None) -> tuple[list[str], Profile | None]:
    """argv for a shell command: sandboxed when enabled, else plain /bin/sh -c."""
    argv = ["/bin/sh", "-c", command]
    s = settings if settings is not None else _settings()
    if not enabled("shell", s):
        return argv, None
    prof = shell_profile(s)
    return prof.wrap(argv), prof


def self_sandboxed(argv: list[str]) -> bool:
    """Does this CLI apply its own Seatbelt profile (which cannot nest in ours)?

    Codex sandboxes the commands it runs (read-only unless told otherwise), so
    only its explicit no-sandbox modes get Aether's profile instead.
    """
    if not argv or os.path.basename(argv[0]) != "codex":
        return False
    if "--dangerously-bypass-approvals-and-sandbox" in argv:
        return False
    if "--sandbox" in argv:
        i = argv.index("--sandbox")
        return not (i + 1 < len(argv) and argv[i + 1] == "danger-full-access")
    return True


_SECRET_ENV_RE = re.compile(r"(?i)(api[_-]?key|token|secret|passw(or)?d|credential|private[_-]?key)")
_KEEP_ENV = frozenset({"SSH_AUTH_SOCK", "TERM_SESSION_ID", "TERM_PROGRAM"})


def child_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for shell commands without Aether's own secrets.

    The sidecar token opens Aether's local APIs (including the native click
    and type endpoint), and provider keys are Aether's, not the command's.
    """
    env = dict(os.environ if base is None else base)
    for key in list(env):
        if key in _KEEP_ENV:
            continue
        if key.startswith("AETHER_") and key not in ("AETHER_SPAWN_DEPTH", "AETHER_DATA_DIR"):
            env.pop(key)
        elif _SECRET_ENV_RE.search(key):
            env.pop(key)
    return env


def wrap_coder(argv: list[str], workspace: str, *,
               settings: dict | None = None) -> tuple[list[str], Profile | None]:
    """argv for a coding agent or agent terminal in ``workspace``."""
    s = settings if settings is not None else _settings()
    if not enabled("coders", s) or self_sandboxed(argv):
        return list(argv), None
    prof = coder_profile(workspace, s)
    return prof.wrap(list(argv)), prof


BLOCKED_HINT = ("Aether's sandbox blocked this ({what}). Writes are limited to "
                "policy.approved_file_roots (plus sandbox.extra_write_roots), credential "
                "files are unreadable, and startup files, git hooks and Aether's own "
                "config are read-only. Ask the user before changing sandbox settings.")


def explain_denial(stderr: str, profile: Profile | None) -> str | None:
    """A hint for the model when a sandboxed command failed on a sandbox rule."""
    if profile is None or not stderr:
        return None
    low = stderr.lower()
    if "sandbox_apply" in low:
        return ("Aether could not start its sandbox here (a sandbox cannot run inside another "
                "one). Set sandbox.coders: false or sandbox.shell: false in config.yaml.")
    if "operation not permitted" in low or "deny(" in low:
        return BLOCKED_HINT.format(what=profile.describe())
    if not profile.network and any(s in low for s in (
            "could not resolve host", "nodename nor servname", "network is unreachable",
            "temporary failure in name resolution")):
        return BLOCKED_HINT.format(what="network is off for this command")
    return None
