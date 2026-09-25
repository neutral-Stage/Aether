"""Static checks on generated tool code before any review or run.

Defense in depth, not the boundary (the sandbox is): an allowlist of
standard-library modules, no dynamic code or reflection, no dunder access,
no process or environment functions reached through any object (so
``shutil.os.system`` is caught as well as ``os.system``), and the required
``run(args)`` entry point.
"""
from __future__ import annotations

import ast

SAFE_MODULES = frozenset({
    "json", "re", "math", "cmath", "datetime", "time", "calendar", "csv", "pathlib", "os",
    "os.path", "statistics", "collections", "collections.abc", "itertools", "functools",
    "string", "textwrap", "unicodedata", "html", "html.parser", "xml", "xml.etree",
    "xml.etree.ElementTree", "hashlib", "hmac", "base64", "binascii", "zipfile", "gzip",
    "bz2", "lzma", "tarfile", "plistlib", "decimal", "fractions", "random", "uuid", "difflib",
    "urllib", "urllib.parse", "shutil", "glob", "fnmatch", "dataclasses", "typing", "enum",
    "io", "struct", "bisect", "heapq", "copy", "pprint", "zlib", "mimetypes", "email",
    "email.utils", "email.message", "email.parser", "email.policy", "quopri", "colorsys",
    "array", "secrets", "wave", "tomllib", "sqlite3",
})
NETWORK_MODULES = frozenset({"urllib.request", "urllib.error", "http", "http.client", "ssl"})
BANNED_CALLS = frozenset({
    "exec", "eval", "compile", "__import__", "globals", "locals", "vars", "getattr", "setattr",
    "delattr", "breakpoint", "input", "memoryview", "open_code", "help", "exit", "quit",
})
# Reached through ANY object: os.system, shutil.os.system, pathlib.os.popen …
BANNED_ATTRS = frozenset({
    "system", "popen", "fork", "forkpty", "kill", "killpg", "execl", "execle", "execlp",
    "execlpe", "execv", "execve", "execvp", "execvpe", "spawnl", "spawnle", "spawnlp",
    "spawnlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe", "posix_spawn", "posix_spawnp",
    "putenv", "unsetenv", "setuid", "setgid", "seteuid", "setegid", "chroot", "startfile",
    "symlink", "link", "attrgetter", "methodcaller", "open_code", "load_extension",
    "enable_load_extension", "system_profiler", "_exit",
})


def check(source: str, *, network: bool = False) -> list[str]:
    """Problems found (empty when the code may go to review)."""
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return [f"syntax error: {e.msg} (line {e.lineno})"]
    allowed = SAFE_MODULES | (NETWORK_MODULES if network else frozenset())
    problems: list[str] = []
    has_run = any(isinstance(n, ast.FunctionDef) and n.name == "run" and len(n.args.args) == 1
                  for n in tree.body)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name not in allowed:
                    problems.append(_import_problem(alias.name, network))
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level or mod not in allowed:
                problems.append(_import_problem(mod or ".", network))
            for alias in node.names:
                full = f"{mod}.{alias.name}"
                if alias.name == "*":
                    problems.append(f"from {mod} import * is not allowed")
                elif alias.name in BANNED_ATTRS:
                    problems.append(f"{full} is not allowed")
                elif mod in ("urllib", "email", "xml", "http", "os") and full not in allowed \
                        and full in (SAFE_MODULES | NETWORK_MODULES):
                    problems.append(_import_problem(full, network))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id in BANNED_CALLS:
            problems.append(f"{node.func.id}() is not allowed")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("__") and node.attr.endswith("__"):
                problems.append(f"dunder attribute .{node.attr} is not allowed")
            elif node.attr in BANNED_ATTRS:
                problems.append(f".{node.attr} is not allowed (it runs programs or "
                                "changes the process)")
            elif node.attr in ("environ", "environb", "getenv") and network:
                problems.append("a tool with internet access cannot read environment "
                                "variables")
        elif isinstance(node, ast.Name) and node.id.startswith("__") and node.id.endswith("__") \
                and node.id != "__name__":
            problems.append(f"{node.id} is not allowed")
    if not has_run:
        problems.append("the tool must define a top-level run(args) function")
    return sorted(set(problems))


def _import_problem(name: str, network: bool) -> str:
    if name in NETWORK_MODULES and not network:
        return f"import {name} needs internet access, which this tool was not given"
    return f"import {name} is not allowed (standard library allowlist only)"
